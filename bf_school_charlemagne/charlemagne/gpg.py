"""OpenPGP for Charlemagne: encrypt for the Ministry and sign as the school, decrypt what comes back.

The guide asks for a file encrypted with the Ministry's public key and signed with the school's
private key, with PGP or GPG, sent in binary mode (section 3.1). This module drives the `gpg`
program in a keyring that exists only for the call: the keys are put in, used, and the whole
directory goes away with the agent. Nothing of a key stays on the disk of the server.

The passphrase goes through a file in that same directory (mode 0600), never on a command line.
"""
import os
import re
import shutil
import subprocess
import tempfile
import time

GPG = "gpg"
TIMEOUT = 60


def scratch_dir():
    """Memory-backed when the server has it: a key never touches the disk."""
    return "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None


def sweep_stale(prefix, max_age=900):
    """Remove what a worker killed in mid-call left behind: older than a quarter of an hour, and ours."""
    base = scratch_dir() or tempfile.gettempdir()
    try:
        names = os.listdir(base)
    except OSError:
        return
    now = time.time()
    for name in names:
        path = os.path.join(base, name)
        try:
            if name.startswith(prefix) and os.path.isdir(path) and now - os.stat(path).st_mtime > max_age \
                    and os.stat(path).st_uid == os.getuid():
                subprocess.run(["gpgconf", "--homedir", path, "--kill", "all"], capture_output=True, timeout=10, check=False)
                shutil.rmtree(path, ignore_errors=True)
        except (OSError, subprocess.SubprocessError):
            continue


class GpgError(Exception):
    """gpg refused, or is not there; the message is what gpg said."""


class Keyring:
    """A throw-away keyring: `with Keyring() as ring:`. Everything is wiped on exit."""

    def __init__(self, passphrase=None):
        self.passphrase = passphrase
        self.home = None
        self.passfile = None

    def __enter__(self):
        if shutil.which(GPG) is None:
            raise GpgError("gpg is not installed on this server.")
        sweep_stale("bfg")
        self.home = tempfile.mkdtemp(prefix="bfg", dir=scratch_dir())
        os.chmod(self.home, 0o700)
        with open(os.path.join(self.home, "gpg-agent.conf"), "w") as conf:
            conf.write("allow-loopback-pinentry\n")
        if self.passphrase:
            self.passfile = os.path.join(self.home, "pass")
            fd = os.open(self.passfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(self.passphrase)
        return self

    def __exit__(self, *exc):
        if self.home:
            try:
                subprocess.run(["gpgconf", "--homedir", self.home, "--kill", "all"],
                               capture_output=True, timeout=10, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
            shutil.rmtree(self.home, ignore_errors=True)
        self.home = self.passfile = None
        return False

    # -- running gpg ---------------------------------------------------------------------

    def _run(self, args, data=None, binary=False):
        command = [GPG, "--homedir", self.home, "--batch", "--no-tty", "--yes"] + list(args)
        try:
            done = subprocess.run(command, input=data, capture_output=True, timeout=TIMEOUT, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GpgError("gpg could not run: %s" % exc) from exc
        return done

    def _pin(self):
        return ["--pinentry-mode", "loopback", "--passphrase-file", self.passfile] if self.passfile else [
            "--pinentry-mode", "loopback", "--passphrase", ""]

    def import_key(self, armored):
        """Put a key (public or secret, armored) in the ring; the fingerprints it holds."""
        data = armored.encode() if isinstance(armored, str) else armored
        done = self._run(["--import"], data)
        if done.returncode != 0:
            raise GpgError("The key could not be read: %s" % done.stderr.decode(errors="replace").strip()[:300])
        return self.fingerprints()

    def fingerprints(self, secret=False):
        done = self._run(["--with-colons", "--list-secret-keys" if secret else "--list-keys"])
        found = []
        for line in done.stdout.decode(errors="replace").splitlines():
            if line.startswith("fpr:"):
                found.append(line.split(":")[9])
        return found

    def has_secret_key(self, fingerprint):
        return fingerprint in self.fingerprints(secret=True)

    def user_ids(self):
        done = self._run(["--with-colons", "--list-keys"])
        return [line.split(":")[9] for line in done.stdout.decode(errors="replace").splitlines() if line.startswith("uid:")]

    # -- the two operations of the transmission -----------------------------------------------

    def sign_and_encrypt(self, data, recipient, signer):
        """Binary OpenPGP message: signed by `signer`, encrypted for `recipient` (fingerprints)."""
        done = self._run(
            self._pin() + ["--trust-model", "always", "--local-user", signer, "--recipient", recipient,
                           "--sign", "--encrypt", "--output", "-"], data)
        if done.returncode != 0 or not done.stdout:
            raise GpgError("The file could not be signed and encrypted: %s"
                           % done.stderr.decode(errors="replace").strip()[:300])
        return done.stdout

    def decrypt_and_verify(self, data, signer=None):
        """(plain bytes, signature_ok, signed_by).

        `signature_ok` is True when a valid signature is there and, if `signer` is given, comes from that
        key (its fingerprint or the primary key of the sub-key that signed). `signed_by` is the fingerprint
        gpg reports, or None.
        """
        args = self._pin() + ["--trust-model", "always", "--status-fd", "2", "--decrypt", "--output", "-"]
        done = self._run(args, data)
        status = done.stderr.decode(errors="replace")
        if "[GNUPG:] DECRYPTION_OKAY" not in status and re.search(
                r"^\[GNUPG:\] (BAD_PASSPHRASE|ERROR pkdecrypt_failed)", status, re.M):
            # The first decryption with a key just imported is refused as a bad passphrase by gpg 2.4 (the agent
            # converts the stored key on first use; the status says `pkdecrypt_failed`, not always `BAD_PASSPHRASE`),
            # and the same call right after works. A truly wrong passphrase fails the second time too.
            done = self._run(args, data)
            status = done.stderr.decode(errors="replace")
        if "[GNUPG:] DECRYPTION_OKAY" not in status:
            raise GpgError("The file could not be decrypted: %s" % self._plain_error(done.stderr))
        signatures = re.findall(r"^\[GNUPG:\] VALIDSIG (\S+)(?: \S+){8} (\S+)", status, re.M)
        if re.search(r"^\[GNUPG:\] (EXPKEYSIG|REVKEYSIG|EXPSIG|BADSIG|ERRSIG) ", status, re.M):
            # An expired or revoked key, or a signature that does not check, is not the Ministry speaking.
            return done.stdout, False, None
        if not signatures:
            return done.stdout, False, None
        fingerprint, primary = signatures[0]
        if signer:
            wanted = signer.upper()
            return done.stdout, wanted in (fingerprint.upper(), primary.upper()), fingerprint
        return done.stdout, True, fingerprint

    @staticmethod
    def _plain_error(stderr):
        lines = [l for l in stderr.decode(errors="replace").splitlines() if not l.startswith("[GNUPG:]")]
        return " ".join(lines)[:300]


def generate_test_key(ring, name, passphrase):
    """A key pair for tests and for the simulator: ed25519 for signing, cv25519 to encrypt."""
    ring.passphrase = passphrase
    ring.passfile = os.path.join(ring.home, "pass")
    with open(ring.passfile, "w") as handle:
        handle.write(passphrase)
    os.chmod(ring.passfile, 0o600)
    done = ring._run(ring._pin() + ["--quick-generate-key", name, "ed25519", "sign", "1d"])
    if done.returncode != 0:
        raise GpgError(done.stderr.decode(errors="replace")[:300])
    fingerprint = ring.fingerprints(secret=True)[-1]
    done = ring._run(ring._pin() + ["--quick-add-key", fingerprint, "cv25519", "encr", "1d"])
    if done.returncode != 0:
        raise GpgError(done.stderr.decode(errors="replace")[:300])
    return fingerprint


def export_key(ring, fingerprint, secret=False):
    """The armored key. A secret key asks for its passphrase twice (unlock, then protect the copy)."""
    args = ["--armor", "--export-secret-keys" if secret else "--export", fingerprint]
    if secret:
        twice = os.path.join(ring.home, "pass2")
        with open(twice, "w") as handle:
            handle.write("%s\n%s\n" % (ring.passphrase, ring.passphrase))
        os.chmod(twice, 0o600)
        args = ["--pinentry-mode", "loopback", "--passphrase-file", twice] + args
    done = ring._run(args)
    if done.returncode != 0:
        raise GpgError(done.stderr.decode(errors="replace")[:300])
    return done.stdout.decode()
