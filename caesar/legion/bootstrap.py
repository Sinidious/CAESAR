"""Worker-bootstrap helpers (ADR-0027).

Used by ``caesar legion new-worker`` to mint a fresh NKEY identity
and print the operator-facing pieces: the seed (to be saved on the
worker host), the public key (to be pasted into
``nats-server.conf``), and the env vars the worker needs.

The seed is generated with :mod:`secrets` for entropy and encoded
with :func:`nkeys.encode_seed` so NATS recognises it as a USER
NKEY. No keys touch the audit log or the DB; they only flow
through stdout for the operator to redirect.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from textwrap import dedent

import nkeys

_NAME_PREFIX = "caesar-worker-"


@dataclass(frozen=True)
class WorkerIdentity:
    """The result of minting a new NKEY identity for a Legion worker.

    NKEY identities are matched by the public key alone — NATS rejects
    ``user:`` alongside ``nkey:`` in ``authorization.users``. The
    ``name`` field is just a human label that flows into the reply
    subject scope (``caesar.reply.<name>.>``); the auth path doesn't
    use it at all.
    """

    name: str
    seed: str
    public_key: str

    @property
    def reply_subject_glob(self) -> str:
        return f"caesar.reply.{self.name}.>"

    def nats_users_block(self) -> str:
        """A snippet to drop under ``authorization.users`` in
        ``nats-server.conf``.

        Includes only the fields NATS accepts for an NKEY identity
        (``nkey:`` + ``permissions:``); the worker name lives in a
        leading comment so operators can grep for it.
        """

        return dedent(
            f"""\
            # {_NAME_PREFIX}{self.name}
            {{ nkey: "{self.public_key}",
              permissions: {{
                publish: {{
                  allow: [
                    "caesar.registry.hello",
                    "caesar.registry.heartbeat",
                    "{self.reply_subject_glob}",
                  ],
                }},
                subscribe: {{
                  allow: [
                    "caesar.dispatch.>",
                    "{self.reply_subject_glob}",
                  ],
                }},
              }},
            }}"""
        )

    def worker_env_vars(self) -> str:
        return dedent(
            f"""\
            CAESAR_BUS__ENABLED=true
            CAESAR_BUS__URL=nats://<praetor-host>:4222
            CAESAR_BUS__AUTH__ENABLED=true
            CAESAR_BUS__AUTH__NKEY_SEED_PATH=/etc/caesar/{self.name}.nkey"""
        )

    def format_for_operator(self) -> str:
        """Human-readable summary suitable for stdout."""

        return dedent(
            f"""\
            === New Legion worker identity: {self.name!r} ===

            1. Save this seed on the worker host (root:root, mode 0600):

               /etc/caesar/{self.name}.nkey

               {self.seed}

               ! DO NOT commit the seed to git. !
               ! DO NOT paste it into chat or screenshots. !

            2. Add the worker to nats-server.conf (under
               authorization.users):

            {self._indent(self.nats_users_block(), 3)}

            3. Reload or restart the nats-server.

            4. On the worker host, point caesar at this identity:

            {self._indent(self.worker_env_vars(), 3)}

            Pair the seed file with the env vars above and the worker
            will register with Praetor over the authed bus on startup.
            """
        )

    @staticmethod
    def _indent(text: str, spaces: int) -> str:
        pad = " " * spaces
        return "\n".join(pad + line for line in text.splitlines())


def generate_worker_identity(*, name: str) -> WorkerIdentity:
    """Mint a fresh ed25519 USER NKEY for a Legion worker."""

    raw = secrets.token_bytes(32)
    seed_bytes = nkeys.encode_seed(raw, nkeys.PREFIX_BYTE_USER)
    seed_str = seed_bytes.decode("ascii")
    kp = nkeys.from_seed(seed_bytes)
    public = kp.public_key.decode("ascii")
    return WorkerIdentity(name=name, seed=seed_str, public_key=public)
