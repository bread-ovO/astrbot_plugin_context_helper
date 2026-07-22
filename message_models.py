from dataclasses import dataclass


@dataclass(frozen=True)
class StoredMessage:
    message_id: str
    sender_name: str
    sender_id: str
    content: str
    created_at_ms: int
