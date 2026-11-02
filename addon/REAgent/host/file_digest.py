"""Bounded streaming digests of native source and observation files."""

import hashlib


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source_file:
        while True:
            chunk = source_file.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)
