module main

import crypto.rand
import crypto.sha1
import encoding.base64
import json2
import net.http
import sync.stdatomic
import veb

fn same_origin(ctx &Context) bool {
	origin := ctx.get_header(.origin) or { return true }
	host := ctx.get_header(.host) or { return false }
	return origin == 'http://${host}' || origin == 'https://${host}'
}

fn api_error(mut ctx Context, status http.Status, message string) veb.Result {
	ctx.res.set_status(status)
	return ctx.json({
		'error': message
	})
}

fn (app &App) authenticate(ctx &Context) int {
	header := ctx.get_header(.authorization) or { return 0 }
	if !header.starts_with('Bearer ') {
		return 0
	}
	token := header.all_after('Bearer ')
	return rlock app.store {
		app.store.tokens[token] or { 0 }
	}
}

pub fn (app &App) before_request(mut ctx Context) {
	ctx.set_header(.cache_control, 'no-store')
	ctx.set_custom_header('X-Content-Type-Options', 'nosniff') or {}
	ctx.set_custom_header('Content-Security-Policy',
		"default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'") or {}
}

@['/']
pub fn (app &App) index(mut ctx Context) veb.Result {
	return ctx.send_response_to_client('text/html; charset=utf-8',
		$embed_file('public/index.html').to_string())
}

@['/style.css']
pub fn (app &App) css(mut ctx Context) veb.Result {
	return ctx.send_response_to_client('text/css; charset=utf-8',
		$embed_file('public/style.css').to_string())
}

@['/app.js']
pub fn (app &App) js(mut ctx Context) veb.Result {
	return ctx.send_response_to_client('text/javascript; charset=utf-8',
		$embed_file('public/app.js').to_string())
}

@['/inter-latin.woff2']
pub fn (app &App) font(mut ctx Context) veb.Result {
	return ctx.send_response_to_client('font/woff2',
		$embed_file('public/inter-latin.woff2').to_string())
}

@['/health']
pub fn (app &App) health(mut ctx Context) veb.Result {
	return ctx.json({
		'status': 'ok'
	})
}

@['/api/register'; post]
pub fn (mut app App) register(mut ctx Context) veb.Result {
	if !same_origin(&ctx) {
		return api_error(mut ctx, .forbidden, 'Origin not allowed')
	}
	if ctx.req.data.len > max_frame {
		return api_error(mut ctx, .request_entity_too_large, 'Request too large')
	}
	input := json2.decode[Registration](ctx.req.data) or {
		return api_error(mut ctx, .bad_request, 'Invalid JSON')
	}
	name := input.username.trim(' \t\r\n')
	if name.len < 3 || name.len > 24 || !name.bytes().all(it.is_alnum() || it == u8(95)) {
		return api_error(mut ctx, .bad_request, 'Use 3–24 letters, numbers, or underscores')
	}
	mut bytes := []u8{len: 32}
	rand.read(mut bytes) or {
		return api_error(mut ctx, .internal_server_error, 'Random source unavailable')
	}
	token := bytes.hex()
	normalized_name := name.to_lower()
	mut user := User{}
	mut conflict := false
	mut full := false
	lock app.store {
		conflict = normalized_name in app.store.names
		full = app.store.users.len >= max_users
		if !conflict && !full {
			user = User{
				id:       app.store.users.len + 1
				username: name
			}
			app.store.names[normalized_name] = user.id
			app.store.tokens[token] = user.id
			app.store.users[user.id] = user
		}
	}
	if conflict {
		return api_error(mut ctx, .conflict, 'Username is taken')
	}
	if full {
		return api_error(mut ctx, .service_unavailable, 'User limit reached')
	}
	app.broadcast_directory()
	ctx.res.set_status(.created)
	return ctx.json(Registered{ user: user, token: token })
}

@['/api/users']
pub fn (app &App) users(mut ctx Context) veb.Result {
	if app.authenticate(&ctx) == 0 {
		return api_error(mut ctx, .unauthorized, 'Unauthorized')
	}
	users := rlock app.store {
		app.store.directory()
	}
	return ctx.json(UsersResponse{ users: users })
}

@['/api/messages/:other']
pub fn (app &App) messages(mut ctx Context, other int) veb.Result {
	id := app.authenticate(&ctx)
	if id == 0 {
		return api_error(mut ctx, .unauthorized, 'Unauthorized')
	}
	mut exists := false
	mut messages := []ChatMessage{}
	rlock app.store {
		exists = other in app.store.users
		if history := app.store.history[conversation_key(id, other)] {
			messages = history.ordered()
		}
	}
	if !exists {
		return api_error(mut ctx, .not_found, 'User not found')
	}
	return ctx.json(MessagesResponse{ messages: messages })
}

@['/ws']
pub fn (mut app App) ws(mut ctx Context) veb.Result {
	if !same_origin(&ctx) {
		return api_error(mut ctx, .forbidden, 'Origin not allowed')
	}
	key := ctx.get_header(.sec_websocket_key) or { '' }
	upgrade := ctx.get_header(.upgrade) or { '' }
	version := ctx.get_custom_header('Sec-WebSocket-Version') or { '' }
	if key == '' || upgrade.to_lower() != 'websocket' || version != '13' {
		return api_error(mut ctx, .bad_request, 'Invalid websocket handshake')
	}
	if stdatomic.load_u64(&app.stopping) != 0 {
		return api_error(mut ctx, .service_unavailable, 'Server shutting down')
	}
	ctx.takeover_conn()
	challenge := sha1.sum((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').bytes())
	digest := base64.encode(challenge)
	response := 'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${digest}\r\n\r\n'
	index := int(stdatomic.fetch_add_u64(&app.next_reactor, 1) % u64(app.reactors.len))
	mut reactor := app.reactors[index]
	reactor.attach(mut ctx.conn, response) or {
		ctx.conn.close() or {}
		return veb.no_result()
	}
	return veb.no_result()
}
