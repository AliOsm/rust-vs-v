mod http;
mod protocol;
mod state;
mod websocket;

use actix_web::{App, HttpServer, middleware::DefaultHeaders, web};
use protocol::MAX_FRAME_BYTES;
use state::Store;
use std::sync::Mutex;

const CONTENT_SECURITY_POLICY: &str = "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'";

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let port: u16 = std::env::var("PORT")
        .unwrap_or_else(|_| "3001".into())
        .parse()
        .expect("valid PORT");
    let host = std::env::var("HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let workers: usize = std::env::var("WORKERS")
        .unwrap_or_else(|_| "2".into())
        .parse()
        .expect("valid WORKERS");
    let state = web::Data::new(Mutex::new(Store::default()));
    println!("Rust / Actix Web listening on http://{host}:{port} ({workers} HTTP workers)");
    HttpServer::new(move || {
        App::new()
            .app_data(state.clone())
            .app_data(web::PayloadConfig::new(MAX_FRAME_BYTES))
            .wrap(
                DefaultHeaders::new()
                    .add(("X-Content-Type-Options", "nosniff"))
                    .add(("Cache-Control", "no-store"))
                    .add(("Content-Security-Policy", CONTENT_SECURITY_POLICY)),
            )
            .configure(http::configure)
    })
    .tcp_nodelay(true)
    .workers(workers)
    .bind((host.as_str(), port))?
    .run()
    .await
}
