module main

import json2

fn test_decoded_frame_values_survive_input_mutation_and_decoder_reuse() ! {
	mut buffer := json2.DecodeBuffer{}
	for offset in 0 .. 16 {
		for text in ['', 'a', '1234567', '12345678', '123456789', 'x'.repeat(4096),
			'é😀'.repeat(257), '\x00\t\n\\"'] {
			expected := Incoming{
				kind:  'send'
				token: 'retained token'
				to:    123
				text:  text
				nonce: 'retained nonce'
			}
			wire := json2.encode(expected)
			// No byte after this allocation belongs to the input, including a terminator.
			mut storage := []u8{len: offset + wire.len}
			unsafe { vmemcpy(&u8(storage.data) + offset, wire.str, wire.len) }
			retained := decode_incoming_frame(storage[offset..], mut buffer)!
			for i in 0 .. storage.len {
				storage[i] = 0xff
			}
			assert retained == expected
			_ := decode_incoming_frame('{"type":"ping","text":"replacement"}'.bytes(), mut buffer)!
			mut rejected := false
			decode_incoming_frame('[0'.bytes(), mut buffer) or { rejected = true }
			assert rejected
			assert retained == expected
		}
	}
}

fn test_borrowed_frame_matches_owned_input_at_truncation_boundaries() {
	mut borrowed := json2.DecodeBuffer{}
	mut owned := json2.DecodeBuffer{}
	for wire in ['{"type":"send","to":2,"text":"é 😀","nonce":"a"}',
		'{"type":"send","text":"\\uD83D\\uDE00\\t\\u0000","extra":[1,{"a":2}]}', '"' + '\t'.repeat(8),
		' {"to":' + '\t'.repeat(8) + '{}}'] {
		for end in 0 .. wire.len + 1 {
			mut payload := []u8{len: end}
			if end > 0 {
				unsafe { vmemcpy(payload.data, wire.str, end) }
			}
			mut expected_error := ''
			expected := json2.decode_reuse[Incoming](payload.bytestr(), mut owned) or {
				expected_error = err.msg()
				Incoming{}
			}
			mut actual_error := ''
			actual := decode_incoming_frame(payload, mut borrowed) or {
				actual_error = err.msg()
				Incoming{}
			}
			assert actual_error == expected_error
			if expected_error == '' {
				assert actual == expected
			}
		}
	}
}
