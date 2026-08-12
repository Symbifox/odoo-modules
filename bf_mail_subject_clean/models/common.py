import re

_REPLY_PREFIX_RE = re.compile(
    r'^(?:\s*re(?:\s*[\[(]\d+[\])])?\s*:\s*)+',
    re.IGNORECASE,
)


def normalize_reply_subject(subject):
    """Collapse stacked reply prefixes like ``Re: Re: Re: Hello`` into a
    single ``Re: Hello``. Returns the input unchanged when no reply prefix
    is detected or when the value is falsy / not a string."""
    if not subject or not isinstance(subject, str):
        return subject
    match = _REPLY_PREFIX_RE.match(subject)
    if not match:
        return subject
    return f'Re: {subject[match.end():]}'
