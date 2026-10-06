//! JSON values and limits shared by the HTTP and WebSocket protocols.
use serde::{Deserialize, Serialize};

pub(crate) const MAX_FRAME_BYTES: usize = 16_384;
pub(crate) const MAX_TEXT_BYTES: usize = 4096;
pub(crate) const MAX_NONCE_BYTES: usize = 64;
pub(crate) const MAX_SESSIONS: usize = 4;

#[derive(Clone, Serialize)]
pub(crate) struct User {
    pub(crate) id: u64,
    pub(crate) username: String,
    pub(crate) online: bool,
}

#[derive(Clone, Serialize)]
pub(crate) struct ChatMessage {
    pub(crate) id: u64,
    pub(crate) from: u64,
    pub(crate) to: u64,
    pub(crate) text: String,
    pub(crate) sent_at: u64,
    pub(crate) nonce: String,
}

#[derive(Deserialize)]
pub(crate) struct Registration {
    pub(crate) username: String,
}

#[derive(Deserialize, Default)]
#[serde(default)]
pub(crate) struct Incoming {
    #[serde(rename = "type")]
    pub(crate) kind: String,
    pub(crate) token: String,
    pub(crate) to: u64,
    pub(crate) text: String,
    pub(crate) nonce: String,
}
