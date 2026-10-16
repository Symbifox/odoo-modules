"""A small SFTP client over the `ssh` program.

The Ministry's server wants SFTP (guide, section 3.1). The Odoo image carries `ssh` and no SFTP
library, and `sftp -b` refuses password prompts, so this speaks the SFTP protocol (version 3, the
common denominator of every server) on the pipes of `ssh -s host sftp`. Errors come back as the
server's status codes instead of text to scrape.

Authentication is `ssh`'s own: a key file, or a password given through SSH_ASKPASS; the server's
host key must be on the line the administrator pasted, `StrictHostKeyChecking=yes`: a school's
credentials are never sent to a host nobody checked.
"""
import os
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import threading

FXP_INIT, FXP_VERSION, FXP_OPEN, FXP_CLOSE, FXP_READ, FXP_WRITE = 1, 2, 3, 4, 5, 6
FXP_OPENDIR, FXP_READDIR, FXP_REMOVE, FXP_RENAME = 11, 12, 13, 18
FXP_STATUS, FXP_HANDLE, FXP_DATA, FXP_NAME = 101, 102, 103, 104
FXF_READ, FXF_WRITE, FXF_CREAT, FXF_TRUNC = 1, 2, 8, 16
OK, EOF, NO_SUCH_FILE = 0, 1, 2
CHUNK = 32768
MAX_PACKET = 1 << 20


class SftpError(Exception):
    """The server said no, or the connection broke."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


#: An account name that cannot be taken for an option or a command by ssh.
SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._@-]*$")
#: A host name likewise, and with no "@": ssh cuts `user@host` at the LAST "@", so a host holding one would move the
#: end of the account name and choose the destination.
SAFE_HOST = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")


def _string(value):
    data = value.encode() if isinstance(value, str) else value
    return struct.pack(">I", len(data)) + data


class SftpClient:
    """`with SftpClient(command, env) as client:` ; `command` starts something that speaks SFTP on its pipes."""

    def __init__(self, command, env=None, timeout=120):
        self.command = command
        self.env = env
        self.timeout = timeout
        self.proc = None
        self.request_id = 0
        self._timer = None

    # -- connection ----------------------------------------------------------------------

    def __enter__(self):
        try:
            self.proc = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        except OSError as exc:
            raise SftpError("The SFTP connection could not start: %s" % exc) from exc
        # A hung server must not hold a worker for ever.
        self._timer = threading.Timer(self.timeout, self._kill)
        self._timer.daemon = True
        self._timer.start()
        self._send(FXP_INIT, struct.pack(">I", 3))
        kind, _body = self._recv()
        if kind != FXP_VERSION:
            raise SftpError("The server did not answer the SFTP handshake: %s" % self._stderr())
        return self

    def __exit__(self, *exc):
        if self._timer:
            self._timer.cancel()
        self._kill()
        return False

    def _kill(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()
            except OSError:
                pass

    def _stderr(self):
        try:
            if self.proc and self.proc.poll() is not None:
                return self.proc.stderr.read().decode(errors="replace").strip()[:300]
        except OSError:
            pass
        return ""

    # -- packets -----------------------------------------------------------------------------

    def _send(self, kind, payload):
        if self.proc is None:
            raise SftpError("The SFTP connection is closed.")
        packet = struct.pack(">IB", len(payload) + 1, kind) + payload
        try:
            self.proc.stdin.write(packet)
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise SftpError("The SFTP connection broke: %s %s" % (exc, self._stderr())) from exc

    def _read_exact(self, size):
        data = b""
        while len(data) < size:
            chunk = self.proc.stdout.read(size - len(data)) if self.proc else b""
            if not chunk:
                raise SftpError("The SFTP connection closed: %s" % self._stderr())
            data += chunk
        return data

    def _recv(self):
        (length,) = struct.unpack(">I", self._read_exact(4))
        if length < 1 or length > MAX_PACKET:
            raise SftpError("The server sent a packet of %s bytes." % length)
        data = self._read_exact(length)
        return data[0], data[1:]

    def _request(self, kind, payload):
        self.request_id += 1
        self._send(kind, struct.pack(">I", self.request_id) + payload)
        answer, body = self._recv()
        (rid,) = struct.unpack(">I", body[:4])
        if rid != self.request_id:
            raise SftpError("The server answered another request.")
        return answer, body[4:]

    def _status(self, body):
        code, = struct.unpack(">I", body[:4])
        (length,) = struct.unpack(">I", body[4:8])
        return code, body[8:8 + length].decode(errors="replace")

    def _expect_ok(self, kind, payload, what):
        answer, body = self._request(kind, payload)
        if answer != FXP_STATUS:
            raise SftpError("%s: unexpected answer %s." % (what, answer))
        code, message = self._status(body)
        if code != OK:
            raise SftpError("%s: %s" % (what, message or "status %s" % code), code)

    def _handle(self, kind, payload, what):
        answer, body = self._request(kind, payload)
        if answer == FXP_STATUS:
            code, message = self._status(body)
            raise SftpError("%s: %s" % (what, message or "status %s" % code), code)
        (length,) = struct.unpack(">I", body[:4])
        return body[4:4 + length]

    # -- operations ----------------------------------------------------------------------------

    def put(self, local_path, remote_path):
        """Upload a file; the remote name is only taken after the last byte, by a rename."""
        partial = remote_path + ".part"
        handle = self._handle(
            FXP_OPEN, _string(partial) + struct.pack(">II", FXF_WRITE | FXF_CREAT | FXF_TRUNC, 0), "open " + partial)
        try:
            offset = 0
            with open(local_path, "rb") as source:
                while True:
                    block = source.read(CHUNK)
                    if not block:
                        break
                    self._expect_ok(FXP_WRITE, _string(handle) + struct.pack(">Q", offset) + _string(block),
                                    "write " + partial)
                    offset += len(block)
        finally:
            self._expect_ok(FXP_CLOSE, _string(handle), "close " + partial)
        try:
            self._expect_ok(FXP_REMOVE, _string(remote_path), "replace " + remote_path)
        except SftpError as exc:
            if exc.code != NO_SUCH_FILE:
                raise
        self._expect_ok(FXP_RENAME, _string(partial) + _string(remote_path), "rename " + partial)

    def get(self, remote_path, local_path):
        handle = self._handle(FXP_OPEN, _string(remote_path) + struct.pack(">II", FXF_READ, 0), "open " + remote_path)
        offset = 0
        try:
            with open(local_path, "wb") as target:
                while True:
                    answer, body = self._request(FXP_READ, _string(handle) + struct.pack(">QI", offset, CHUNK))
                    if answer == FXP_STATUS:
                        code, message = self._status(body)
                        if code == EOF:
                            break
                        raise SftpError("read %s: %s" % (remote_path, message), code)
                    (length,) = struct.unpack(">I", body[:4])
                    target.write(body[4:4 + length])
                    offset += length
        finally:
            self._expect_ok(FXP_CLOSE, _string(handle), "close " + remote_path)
        return offset

    def listdir(self, remote_dir):
        handle = self._handle(FXP_OPENDIR, _string(remote_dir), "open directory " + remote_dir)
        names = []
        try:
            while True:
                answer, body = self._request(FXP_READDIR, _string(handle))
                if answer == FXP_STATUS:
                    code, message = self._status(body)
                    if code == EOF:
                        break
                    raise SftpError("list %s: %s" % (remote_dir, message), code)
                (count,) = struct.unpack(">I", body[:4])
                position = 4
                for _ in range(count):
                    (length,) = struct.unpack(">I", body[position:position + 4])
                    name = body[position + 4:position + 4 + length].decode(errors="replace")
                    position += 4 + length
                    (length,) = struct.unpack(">I", body[position:position + 4])
                    position += 4 + length  # the long name
                    position = self._skip_attrs(body, position)
                    if name not in (".", ".."):
                        names.append(name)
        finally:
            self._expect_ok(FXP_CLOSE, _string(handle), "close directory " + remote_dir)
        return sorted(names)

    @staticmethod
    def _skip_attrs(body, position):
        (flags,) = struct.unpack(">I", body[position:position + 4])
        position += 4
        if flags & 0x1:
            position += 8
        if flags & 0x2:
            position += 8
        if flags & 0x4:
            position += 4
        if flags & 0x8:
            position += 8
        if flags & 0x80000000:
            (count,) = struct.unpack(">I", body[position:position + 4])
            position += 4
            for _ in range(count * 2):
                (length,) = struct.unpack(">I", body[position:position + 4])
                position += 4 + length
        return position

    def remove(self, remote_path):
        self._expect_ok(FXP_REMOVE, _string(remote_path), "remove " + remote_path)


class SshSftp:
    """What the school's server needs: connect to the Ministry's SFTP with a key or a password."""

    def __init__(self, host, port, user, password=None, private_key=None, known_host=None, timeout=90):
        if not known_host:
            raise SftpError("The server's host key is not entered: credentials are not sent to an unchecked host.")
        for label, value, plain in (("host", host, SAFE_HOST), ("account", user, SAFE_NAME)):
            if not value or not plain.match(str(value)):
                raise SftpError("The SFTP %s is not a plain name: letters, digits, dots, dashes and underscores." % label)
        self.host, self.port, self.user = host, int(port or 22), user
        self.password, self.private_key, self.known_host = password, private_key, known_host
        self.timeout = timeout
        self._dir = None

    def _prepare(self):
        from .gpg import scratch_dir, sweep_stale
        sweep_stale("bfs")
        self._dir = tempfile.mkdtemp(prefix="bfs", dir=scratch_dir())
        os.chmod(self._dir, 0o700)
        known = os.path.join(self._dir, "known_hosts")
        with open(known, "w") as handle:
            handle.write(self.known_host.strip() + "\n")
        command = ["ssh", "-p", str(self.port), "-o", "UserKnownHostsFile=" + known,
                   "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=15",
                   "-o", "NumberOfPasswordPrompts=1", "-o", "GlobalKnownHostsFile=/dev/null", "-s"]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self._dir, "LANG": "C"}
        if self.private_key:
            key = os.path.join(self._dir, "key")
            fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(self.private_key if self.private_key.endswith("\n") else self.private_key + "\n")
            command += ["-i", key, "-o", "IdentitiesOnly=yes", "-o", "PreferredAuthentications=publickey",
                        "-o", "BatchMode=yes"]
        else:
            askpass = os.path.join(self._dir, "askpass")
            with open(askpass, "w") as handle:
                handle.write('#!/bin/sh\nprintf "%s\\n" "$BF_SFTP_PASSWORD"\n')
            os.chmod(askpass, stat.S_IRWXU)
            env.update({"SSH_ASKPASS": askpass, "SSH_ASKPASS_REQUIRE": "force", "BF_SFTP_PASSWORD": self.password or "",
                        "DISPLAY": ":0"})
            command += ["-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no"]
        command += ["--", "%s@%s" % (self.user, self.host), "sftp"]
        return command, env

    def __enter__(self):
        command, env = self._prepare()
        self._client = SftpClient(command, env, self.timeout)
        try:
            return self._client.__enter__()
        except SftpError:
            self.__exit__()
            raise

    def __exit__(self, *exc):
        try:
            if getattr(self, "_client", None):
                self._client.__exit__()
        finally:
            if self._dir:
                shutil.rmtree(self._dir, ignore_errors=True)
                self._dir = None
        return False
