# Channel Showdown: False Success (same page, same click, four readouts)

> Same page, same click: A naive DOM / P official Playwright MCP / B receipt (this project) / C hidden ground truth.
> Arm P is a **real invocation** of @playwright/mcp v0.0.82 with default config; raw responses quoted verbatim.
> Reproduce: `python mcp/demo_showdown.py --case all --report`

## Summary

| Case | Backend reality | Failure cause in P's response? | B verdict |
|---|---|---|---|
| Page shows success, backend returns 422 | HTTP 422 | no (console count only) | `errored` |
| Button greys out, request rolls back (500) | HTTP 500 | no (console count only) | `errored` |
| 202 accepted, async task fails 3 s later | HTTP 202; HTTP 500 | no (console count only) | `unchanged` |
## Fairness notes (read this)

1. **Arm P is not signal-free**: the click response carries a console error **count** (this page always includes a favicon-404 noise line). A strong model may chase it; but the concrete status code and cause (422 / email taken) are absent from the response text and require **1-3 additional, model-initiated calls** (read the log file → `browser_network_requests` → `browser_network_request` body).
2. **Arm B puts the verdict and the structured cause inside the action response itself.** The contrast is **channel shape (push vs pull)**, not model capability — consistent with this project's own A/B finding that model-initiated verification calls don't happen (1 call in 15 turns).
3. Snapshots being returned as **file links** is the observed default-config behavior of v0.0.82 on this machine and may vary by client/config; every response here is quoted verbatim, so the report does not depend on that behavior.
4. Case 3 (202 + async failure): arm B also only reports `unchanged/uncertain` — async-result tracking is a registered gap. Both arms are blind here; neither is better.

---

## Case: Page shows success, backend returns 422

| Readout | What the action response contains | Extra calls needed to reach the truth |
|---|---|---|
| **A Naive DOM** | `✅ 注册成功!` | never (the page never shows the failure) |
| **P Playwright MCP** (v0.0.82) | `isError=false` + `Console: 2 errors, 0 warnings` (count only, incl. a favicon-404 noise line) + snapshot/console linked as **files** | ≥2 hops: read the log file or call `browser_network_requests`, then `browser_network_request` for the body |
| **B Receipt** (this project) | `page_outcome=errored` / `操作引发后端接口报错: HTTP 422 http://127.0.0.1:63326/external_facts.html?simulate=422 — {"error": "email_taken", "message": "账号已存在"}` | **0** (verdict + cause inside the response) |
| **C Ground truth** (invisible to agent) | `/external_facts.html?simulate=422 → HTTP 422` | — |

### Arm P: raw click response (verbatim)

````
### Ran Playwright code
```js
await page.locator('#btn1').click();
```
### Page
- Page URL: http://127.0.0.1:63326/external_facts.html
- Page Title: 外部事实五反例(核心命题 §2.2)
- Console: 2 errors, 0 warnings
### Snapshot
- [Snapshot](C:\Users\ADMINI~1\AppData\Local\Temp\pw_mcp_showdown_w4_q25je\page-2026-09-19T16-21-51-331Z.yml)
### Events
- New console entries: C:\Users\ADMINI~1\AppData\Local\Temp\pw_mcp_showdown_w4_q25je\console-2026-09-19T16-21-49-395Z.log#L1-L2
````

> P arm's browser triggered the same fault (server-side record): `/external_facts.html?simulate=422 → HTTP 422`

### Arm P, hop 1: browser_network_requests (requires the model to call it)

```
### Result
2. [POST] http://127.0.0.1:63326/external_facts.html?simulate=422 => [422] Unprocessable Entity

Note: 1 static request not shown, run with "static" option to see it.
```

### Arm P: linked console log file (requires another read)

```
[     679ms] [ERROR] Failed to load resource: the server responded with a status of 404 (Not Found) @ http://127.0.0.1:63326/favicon.ico:0
[     913ms] [ERROR] Failed to load resource: the server responded with a status of 422 (Unprocessable Entity) @ http://127.0.0.1:63326/external_facts.html?simulate=422:0
```

### Arm B: key receipt fields

```json
{
 "page_outcome": "errored",
 "situation": "network_error",
 "why": "操作引发后端接口报错: HTTP 422 http://127.0.0.1:63326/external_facts.html?simulate=422 — {\"error\": \"email_taken\", \"message\": \"账号已存在\"}",
 "errors": [
  {
   "url": "http://127.0.0.1:63326/external_facts.html?simulate=422",
   "status": 422,
   "kind": "http_error",
   "detail": "{\"error\": \"email_taken\", \"message\": \"账号已存在\"}"
  }
 ]
}
```

---

## Case: Button greys out, request rolls back (500)

| Readout | What the action response contains | Extra calls needed to reach the truth |
|---|---|---|
| **A Naive DOM** | `处理中…` | wait and re-read the page (it flips at ~3 s) |
| **P Playwright MCP** (v0.0.82) | `isError=false` + `Console: 2 errors, 0 warnings` (count only, incl. a favicon-404 noise line) + snapshot/console linked as **files** | ≥2 hops: read the log file or call `browser_network_requests`, then `browser_network_request` for the body |
| **B Receipt** (this project) | `page_outcome=errored` / `操作引发后端接口报错: HTTP 500 http://127.0.0.1:63326/external_facts.html?simulate=500 — {"error": "payment_rollback", "message": "支付失败,已回滚"}` | **0** (verdict + cause inside the response) |
| **C Ground truth** (invisible to agent) | `/external_facts.html?simulate=500 → HTTP 500` | — |

3.5 s later the page itself flips to `❌ 支付失败,已回滚` (outside the action window; visible to A/P only on a deliberate re-check). P-arm deliberate re-check (`browser_wait_for ❌`): visible ✓

### Arm P: raw click response (verbatim)

````
### Ran Playwright code
```js
await page.locator('#btn2').click();
```
### Page
- Page URL: http://127.0.0.1:63326/external_facts.html
- Page Title: 外部事实五反例(核心命题 §2.2)
- Console: 2 errors, 0 warnings
### Snapshot
- [Snapshot](C:\Users\ADMINI~1\AppData\Local\Temp\pw_mcp_showdown_jb7orpvg\page-2026-09-19T16-22-08-423Z.yml)
### Events
- New console entries: C:\Users\ADMINI~1\AppData\Local\Temp\pw_mcp_showdown_jb7orpvg\console-2026-09-19T16-22-06-459Z.log#L1-L2
````

> P arm's browser triggered the same fault (server-side record): `/external_facts.html?simulate=500 → HTTP 500`

### Arm P, hop 1: browser_network_requests (requires the model to call it)

```
### Result
2. [POST] http://127.0.0.1:63326/external_facts.html?simulate=500 => [500] Internal Server Error

Note: 1 static request not shown, run with "static" option to see it.
```

### Arm P: linked console log file (requires another read)

```
[     690ms] [ERROR] Failed to load resource: the server responded with a status of 404 (Not Found) @ http://127.0.0.1:63326/favicon.ico:0
[     912ms] [ERROR] Failed to load resource: the server responded with a status of 500 (Internal Server Error) @ http://127.0.0.1:63326/external_facts.html?simulate=500:0
```

### Arm B: key receipt fields

```json
{
 "page_outcome": "errored",
 "situation": "network_error",
 "why": "操作引发后端接口报错: HTTP 500 http://127.0.0.1:63326/external_facts.html?simulate=500 — {\"error\": \"payment_rollback\", \"message\": \"支付失败,已回滚\"}",
 "errors": [
  {
   "url": "http://127.0.0.1:63326/external_facts.html?simulate=500",
   "status": 500,
   "kind": "http_error",
   "detail": "{\"error\": \"payment_rollback\", \"message\": \"支付失败,已回滚\"}"
  }
 ]
}
```

---

## Case: 202 accepted, async task fails 3 s later

| Readout | What the action response contains | Extra calls needed to reach the truth |
|---|---|---|
| **A Naive DOM** | `✅ 已提交,任务排队中(202)` | wait and re-read the page (it flips at ~3 s) |
| **P Playwright MCP** (v0.0.82) | `isError=false` + `Console: 1 errors, 0 warnings` (count only, incl. a favicon-404 noise line) + snapshot/console linked as **files** | ≥2 hops: read the log file or call `browser_network_requests`, then `browser_network_request` for the body |
| **B Receipt** (this project) | `page_outcome=unchanged` / `目标区域无变化(点击可能未生效,或效果发生在远处)` | **0** (verdict + cause inside the response) |
| **C Ground truth** (invisible to agent) | `/external_facts.html?simulate=202 → HTTP 202; /external_facts.html?poll=task&simulate=500 → HTTP 500` | — |

3.5 s later the page itself flips to `❌ 后台任务失败(3 秒后)` (outside the action window; visible to A/P only on a deliberate re-check). P-arm deliberate re-check (`browser_wait_for ❌`): visible ✓

### Arm P: raw click response (verbatim)

````
### Ran Playwright code
```js
await page.locator('#btn3').click();
```
### Page
- Page URL: http://127.0.0.1:63326/external_facts.html
- Page Title: 外部事实五反例(核心命题 §2.2)
- Console: 1 errors, 0 warnings
### Snapshot
- [Snapshot](C:\Users\ADMINI~1\AppData\Local\Temp\pw_mcp_showdown_vruie45b\page-2026-09-19T16-22-27-476Z.yml)
### Events
- New console entries: C:\Users\ADMINI~1\AppData\Local\Temp\pw_mcp_showdown_vruie45b\console-2026-09-19T16-22-25-413Z.log#L1
````

> P arm's browser triggered the same fault (server-side record): `/external_facts.html?simulate=202 → HTTP 202; /external_facts.html?poll=task&simulate=500 → HTTP 500`

### Arm P, hop 1: browser_network_requests (requires the model to call it)

```
### Result
2. [POST] http://127.0.0.1:63326/external_facts.html?simulate=202 => [202] Accepted

Note: 1 static request not shown, run with "static" option to see it.
```

### Arm P: linked console log file (requires another read)

```
[     803ms] [ERROR] Failed to load resource: the server responded with a status of 404 (Not Found) @ http://127.0.0.1:63326/favicon.ico:0
```

### Arm B: key receipt fields

```json
{
 "page_outcome": "unchanged",
 "situation": "none",
 "why": "目标区域无变化(点击可能未生效,或效果发生在远处)",
 "errors": null
}
```
