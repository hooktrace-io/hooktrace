# n8n webhooks

## What hooktrace validates

When you configure `n8n` as your signature provider in the viewer's Settings
panel, hooktrace verifies each captured request's `X-N8N-Signature` header
against the secret you configured on the n8n webhook node.

## Signature header

- Header name: `X-N8N-Signature` (canonical form; lowercase variants are
  accepted because n8n's HTTP client folds header case).
- Algorithm: HMAC-SHA256
- Secret format: an opaque string set in the webhook node's authentication
  configuration.
- Signed payload: the raw request body, byte for byte.
- Header format: a hex-encoded digest.

## Where to find the secret

In the n8n workflow editor, open the **Webhook** trigger node →
**Authentication → Header Auth / Signature** and read or generate the secret
that the node will use to sign outbound requests.

## Sample payload (truncated)

```json
{
  "workflow_id": "wf_xyz",
  "execution_id": "exec_456",
  "data": {
    "node": "When New Order",
    "json": {
      "order_id": "ord_001",
      "total": 49.99
    }
  }
}
```

## Reference

- Official docs: <https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/>
