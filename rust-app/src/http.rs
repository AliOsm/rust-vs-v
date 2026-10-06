//! HTTP routes, authentication, and embedded frontend assets.
use crate::{
    protocol::{Registration, User},
    state::{MAX_USERS, State, broadcast_directory, conversation_key},
    websocket,
};
use actix_web::{HttpRequest, HttpResponse, Responder, http::StatusCode, web};
use serde_json::json;

pub(crate) fn error(status: StatusCode, message: &str) -> HttpResponse {
    HttpResponse::build(status).json(json!({"error": message}))
}

fn auth(req: &HttpRequest, state: &State) -> Option<u64> {
    let token = req
        .headers()
        .get("authorization")?
        .to_str()
        .ok()?
        .strip_prefix("Bearer ")?;
    state.lock().unwrap().tokens.get(token).copied()
}

pub(crate) fn same_origin(req: &HttpRequest) -> bool {
    let Some(origin) = req.headers().get("origin") else {
        return true;
    };
    let Ok(origin) = origin.to_str() else {
        return false;
    };
    let host = req
        .headers()
        .get("host")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    origin
        .strip_prefix("http://")
        .or_else(|| origin.strip_prefix("https://"))
        == Some(host)
}

async fn register(req: HttpRequest, body: web::Bytes, state: State) -> HttpResponse {
    if !same_origin(&req) {
        return error(StatusCode::FORBIDDEN, "Origin not allowed");
    }
    let Ok(input) = serde_json::from_slice::<Registration>(&body) else {
        return error(StatusCode::BAD_REQUEST, "Invalid JSON");
    };
    let name = input.username.trim_matches([' ', '\t', '\r', '\n']);
    if !(3..=24).contains(&name.len())
        || !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
    {
        return error(
            StatusCode::BAD_REQUEST,
            "Use 3–24 letters, numbers, or underscores",
        );
    }
    let mut bytes = [0u8; 32];
    if getrandom::fill(&mut bytes).is_err() {
        return error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Random source unavailable",
        );
    }
    let token: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
    let normalized_name = name.to_ascii_lowercase();
    let user = {
        let mut store = state.lock().unwrap();
        if store.names.contains_key(&normalized_name) {
            return error(StatusCode::CONFLICT, "Username is taken");
        }
        if store.users.len() >= MAX_USERS {
            return error(StatusCode::SERVICE_UNAVAILABLE, "User limit reached");
        }
        let user = User {
            id: store.users.len() as u64 + 1,
            username: name.to_owned(),
            online: false,
        };
        store.names.insert(normalized_name, user.id);
        store.tokens.insert(token.clone(), user.id);
        store.users.insert(user.id, user.clone());
        user
    };
    broadcast_directory(&state);
    HttpResponse::Created().json(json!({"user": user, "token": token}))
}

async fn users(req: HttpRequest, state: State) -> HttpResponse {
    if auth(&req, &state).is_none() {
        return error(StatusCode::UNAUTHORIZED, "Unauthorized");
    }
    HttpResponse::Ok().json(json!({"users": state.lock().unwrap().directory()}))
}

async fn history(req: HttpRequest, other: web::Path<u64>, state: State) -> HttpResponse {
    let Some(id) = auth(&req, &state) else {
        return error(StatusCode::UNAUTHORIZED, "Unauthorized");
    };
    let store = state.lock().unwrap();
    if !store.users.contains_key(&other) {
        return error(StatusCode::NOT_FOUND, "User not found");
    }
    let messages = store
        .history
        .get(&conversation_key(id, *other))
        .cloned()
        .unwrap_or_default();
    HttpResponse::Ok().json(json!({"messages": messages}))
}

async fn index() -> impl Responder {
    HttpResponse::Ok()
        .content_type("text/html; charset=utf-8")
        .body(include_str!("../public/index.html"))
}
async fn css() -> impl Responder {
    HttpResponse::Ok()
        .content_type("text/css; charset=utf-8")
        .body(include_str!("../public/style.css"))
}
async fn js() -> impl Responder {
    HttpResponse::Ok()
        .content_type("text/javascript; charset=utf-8")
        .body(include_str!("../public/app.js"))
}
async fn font() -> impl Responder {
    HttpResponse::Ok()
        .content_type("font/woff2")
        .body(include_bytes!("../public/inter-latin.woff2").as_slice())
}

pub(crate) fn configure(routes: &mut web::ServiceConfig) {
    routes
        .route("/", web::get().to(index))
        .route("/style.css", web::get().to(css))
        .route("/app.js", web::get().to(js))
        .route("/inter-latin.woff2", web::get().to(font))
        .route("/health", web::get().to(health))
        .route("/api/register", web::post().to(register))
        .route("/api/users", web::get().to(users))
        .route("/api/messages/{other}", web::get().to(history))
        .route("/ws", web::get().to(websocket::upgrade));
}

async fn health() -> impl Responder {
    HttpResponse::Ok().json(json!({"status": "ok"}))
}
