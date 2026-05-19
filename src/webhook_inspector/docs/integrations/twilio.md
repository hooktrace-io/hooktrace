# Twilio webhooks

## What hooktrace validates

When you configure `twilio` as your signature provider in the viewer's
Settings panel, hooktrace verifies each captured request's
`X-Twilio-Signature` header against your Twilio account's Auth Token.

## Signature header

- Header name: `X-Twilio-Signature`
- Algorithm: HMAC-SHA1
- Secret format: the **Auth Token** from your Twilio account (32 hex
  characters).
- Signed payload (Twilio's canonical scheme): the full request URL
  concatenated with the sorted form-parameter `key=value` pairs (no
  separators).
- Header format: a base64-encoded digest.

## Caveat — hooktrace's simplified validation

Twilio's canonical signature is computed over the request URL **plus** sorted
form parameters. hooktrace currently validates HMAC-SHA1 over the raw body
bytes only, which matches when the body itself is the canonical form-encoded
string and the URL component is not part of the input.

Full URL-aware Twilio validation (passing the original request URL through
the validation context) is planned for a later version. Until then, expect
some legitimate Twilio webhooks to show `INVALID` in the viewer — the captured
payload is still trustworthy, but the signature check is not strict.

## Where to find the secret

Twilio Console → top-right account menu → **Account → API keys & tokens →
Auth Token**.

## Sample payload (truncated, form-encoded)

```
MessageSid=SMxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
AccountSid=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
From=%2B14155552671
To=%2B14155552672
Body=Hello+from+Twilio
NumSegments=1
```

## Reference

- Official docs: <https://www.twilio.com/docs/usage/webhooks/webhooks-security>
