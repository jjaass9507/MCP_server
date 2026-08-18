# Windows + IIS + Corporate CA HTTPS Deployment Plan

> **Purpose**: Convert the current Windows-hosted MCP Server from a PowerShell-attached HTTP process into a production-style internal service that starts automatically, is exposed through corporate HTTPS, preserves Bearer authentication, and supports cross-machine export downloads over HTTPS.
>
> **Audience**: Human maintainers and AI coding/deployment agents. This document is intentionally written as an executable handoff plan. A new agent should be able to continue from the first unchecked item without needing the original conversation.
>
> **Repository**: `jjaass9507/MCP_server`
>
> **Primary runtime**: Offline / air-gapped Windows server
>
> **Target transport**: MCP Streamable HTTP

---

## 0. Executive target

The final deployment should look like this:

```text
AI Agent / MCP Client
        |
        | HTTPS 443
        | Authorization: Bearer <token>
        v
+--------------------------------------+
| IIS                                  |
| Corporate CA certificate             |
| TLS termination / reverse proxy      |
|                                      |
| /mcp     -> 127.0.0.1:8080/mcp      |
| /exports -> 127.0.0.1:8081/exports  |
+-------------------+------------------+
                    |
                    | local HTTP only
                    v
+--------------------------------------+
| MCP Server Windows Service           |
| Python .venv                         |
| Streamable HTTP                      |
| 127.0.0.1:8080                       |
| BearerTokenMiddleware remains active |
+--------------------------------------+

Optional export server:
127.0.0.1:8081 -> IIS /exports -> HTTPS 443
```

### Target external URLs

```text
https://<MCP_FQDN>/mcp
https://<MCP_FQDN>/exports/<id>
```

Example only:

```text
https://mcp.internal.example.com/mcp
```

Do **not** standardize on an IP URL when using a corporate certificate unless the certificate SAN explicitly contains that IP. Prefer the DNS/FQDN issued in the certificate.

---

## 1. Current repository baseline

Before changing anything, verify the repository still matches these assumptions.

### MCP HTTP runtime

Current implementation is in:

```text
src/mcp_server/server.py
```

Expected behavior:

- `FastMCP` is configured with `streamable_http_path="/mcp"`.
- Streamable HTTP is served with `uvicorn.run(...)`.
- Bearer authentication is wrapped around the ASGI app by `BearerTokenMiddleware`.
- Server-side TLS is not currently configured in Uvicorn.
- Database pools are closed from the `finally` block during normal server shutdown.

### HTTP configuration

Current example config is:

```text
config.toml.example
```

Expected `[http]` fields include:

```toml
host = "127.0.0.1"
port = 8080
allowed_hosts = [...]
allowed_origins = [...]
bearer_token_env = "MCP_HTTP_BEARER_TOKEN"
```

### Current Windows deployment gap

The repository currently contains Linux service deployment files:

```text
deploy/install-systemd.sh
deploy/mcp-server.service
```

There is no equivalent Windows Service deployment package yet.

### Current export/download behavior

Implementation:

```text
src/mcp_server/utils/download_server.py
```

The current generated cross-machine URL is HTTP and includes `advertise_host` + `download_port` directly.

This means that putting only `/mcp` behind HTTPS is insufficient if `serve_downloads = true`; `/exports` also needs an externally advertised HTTPS URL.

---

## 2. Non-negotiable design decisions

Agents must preserve these decisions unless the maintainer explicitly changes them.

### 2.1 IIS terminates TLS

Do **not** make Python/Uvicorn the primary corporate certificate owner.

Reason:

- Corporate certificates are normally managed in Windows Certificate Store.
- Private keys may be marked non-exportable.
- IIS can bind directly to certificates in the Windows certificate store.
- Certificate renewal can then happen independently from MCP application code.

Therefore:

```text
External: HTTPS -> IIS
Internal: IIS -> HTTP 127.0.0.1
```

### 2.2 MCP must bind to localhost after IIS is enabled

Production target:

```toml
[http]
host = "127.0.0.1"
port = 8080
```

Do not expose MCP's backend port directly to the LAN after IIS reverse proxy is active.

### 2.3 Bearer authentication remains enabled

HTTPS does not replace MCP authorization.

Required path:

```text
Client
  -> HTTPS
  -> IIS
  -> Authorization: Bearer <token>
  -> BearerTokenMiddleware
  -> MCP
```

Do not remove `BearerTokenMiddleware` simply because IIS is using HTTPS.

### 2.4 Corporate certificate trust must exist on clients

The MCP server possessing a valid certificate is only one side of TLS.

Every consuming machine must trust the issuing corporate CA chain:

```text
Corporate Root CA
  -> Intermediate CA (if used)
  -> MCP server certificate
```

Do not disable TLS verification in the Agent as the production solution.

### 2.5 Offline deployment remains supported

The production machine is treated as offline / restricted-network Windows.

Any new runtime dependency such as WinSW must be included in the offline packaging/deployment process rather than downloaded dynamically on the production server.

---

## 3. Definition of Done

Deployment is complete only when all of the following are true.

- [ ] MCP is automatically started after Windows boot.
- [ ] Closing PowerShell does not stop MCP.
- [ ] MCP backend listens only on `127.0.0.1:8080`.
- [ ] IIS listens on HTTPS 443 using the corporate-issued certificate.
- [ ] `https://<MCP_FQDN>/mcp` reaches MCP through IIS.
- [ ] Bearer authentication still rejects requests without the correct token.
- [ ] Correct Bearer token succeeds through IIS.
- [ ] Client TLS verification succeeds without disabling certificate verification.
- [ ] Rebooting the machine restores the service automatically.
- [ ] Python process failure triggers automatic service recovery/restart.
- [ ] Logs are persisted outside an interactive PowerShell session.
- [ ] Existing database connection-pool cleanup behavior is preserved.
- [ ] If exports are enabled, returned `download_url` is HTTPS and externally reachable.
- [ ] Backend ports 8080/8081 are not exposed to general LAN clients.
- [ ] Deployment procedure is documented and repeatable on a fresh Windows host.

---

# Phase A — Collect infrastructure values

**Owner**: Infrastructure / Windows administrator

**Code change required**: No

**Blocking**: Yes

Before coding deployment behavior, collect the actual production values.

## A1. Required values

Fill these in before Phase D.

```text
MCP_FQDN=
MCP_SERVER_HOSTNAME=
MCP_SERVER_IP=
MCP_HTTPS_PORT=443
MCP_BACKEND_HOST=127.0.0.1
MCP_BACKEND_PORT=8080
MCP_EXPORT_BACKEND_PORT=8081
CERTIFICATE_SUBJECT=
CERTIFICATE_THUMBPRINT=
CORPORATE_CA_NAME=
WINDOWS_SERVICE_ACCOUNT=
PROJECT_ROOT=
PYTHON_VENV_PATH=
CONFIG_TOML_PATH=
LOG_DIRECTORY=
```

Recommended project root example:

```text
D:\FAC_Job\MCP_server
```

## A2. DNS verification

The DNS record must resolve from MCP client machines.

```powershell
Resolve-DnsName <MCP_FQDN>
```

Expected result: the intended MCP Windows server IP.

## A3. Certificate verification

On the MCP Windows server:

1. Open `certlm.msc`.
2. Check `Local Computer -> Personal -> Certificates`.
3. Locate the corporate certificate.
4. Confirm:
   - certificate is not expired;
   - certificate has an associated private key;
   - Server Authentication EKU is present;
   - Subject Alternative Name contains `<MCP_FQDN>`;
   - certificate chain is valid.

PowerShell inspection example:

```powershell
Get-ChildItem Cert:\LocalMachine\My |
    Select-Object Subject, Thumbprint, NotAfter, HasPrivateKey
```

### A acceptance criteria

- [ ] FQDN exists.
- [ ] Certificate SAN matches FQDN.
- [ ] Server has access to certificate private key.
- [ ] Client machines trust the corporate CA chain.

---

# Phase B — Add Windows Service deployment support

**Owner**: Coding agent

**Code change required**: Yes

**Recommended implementation**: WinSW wrapper

The Python process itself is not a native Windows Service. Do not treat a scheduled interactive PowerShell window as the final production solution.

## B1. Add deployment files

Create:

```text
deploy/windows/
  MCPServer.xml
  install-service.ps1
  uninstall-service.ps1
  start-service.ps1
  stop-service.ps1
  restart-service.ps1
  README.md
```

WinSW executable should be packaged using a stable name, for example:

```text
deploy/windows/MCPServer.exe
```

Do not make the production machine download WinSW from the internet.

## B2. Service command

The service must run the project venv Python directly.

Logical command:

```text
<PROJECT_ROOT>\.venv\Scripts\python.exe
    -m mcp_server.server
    --transport streamable-http
```

The service working directory must be the repository root because existing project behavior depends on the current working directory unless `MCP_CONFIG` is explicitly set.

Preferred approach: set both the working directory and `MCP_CONFIG` explicitly.

Example service environment:

```text
MCP_CONFIG=D:\FAC_Job\MCP_server\config.toml
MCP_HTTP_BEARER_TOKEN=<provided securely>
MCP_LOG_LEVEL=INFO
MCP_LOG_FILE=D:\FAC_Job\MCP_server\logs\mcp-server.log
```

## B3. Service identity

Do not assume the interactive user's environment variables will exist in a Windows Service.

Choose one of these explicitly:

1. Dedicated domain/service account — preferred when database/network access requires domain identity.
2. Local service account — acceptable only if all required resources are reachable under that identity.

Verify that the selected account can:

- read project files;
- read `config.toml`;
- execute `.venv\Scripts\python.exe`;
- write the log directory;
- write export directory if enabled;
- reach configured PostgreSQL / MSSQL / Oracle endpoints;
- read any filesystem paths exposed by MCP.

## B4. Recovery behavior

Configure automatic restart on process failure.

Required intent:

```text
First failure  -> restart service
Second failure -> restart service
Subsequent     -> restart service
```

Avoid an infinite rapid crash loop. Use an appropriate restart delay such as 5–10 seconds.

## B5. Startup type

Target:

```text
Startup Type = Automatic
```

Automatic (Delayed Start) is acceptable if Oracle/network dependencies are not ready immediately during machine boot.

## B6. Windows Service smoke test

After installing:

```powershell
Get-Service MCPServer
Start-Service MCPServer
Get-Service MCPServer
```

Verify backend listener:

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen
```

Expected:

```text
LocalAddress = 127.0.0.1
```

Then close all interactive PowerShell windows and verify the listener/service remains alive.

### B acceptance criteria

- [ ] `MCPServer` exists as a Windows Service.
- [ ] Service starts without interactive login.
- [ ] PowerShell window closure has no impact.
- [ ] Backend binds to localhost only.
- [ ] Log file is written successfully.
- [ ] Service restarts after forced Python process termination.

---

# Phase C — Integrate Windows deployment into offline packaging

**Owner**: Coding agent

**Code change required**: Yes

Inspect and update:

```text
scripts/pack_offline.ps1
scripts/install_offline.ps1
```

## C1. Pack WinSW with offline bundle

The online packaging machine may download/package the approved WinSW binary, but the offline target machine must not require internet access.

The final offline ZIP must contain all Windows service deployment assets.

## C2. Do not silently install the service during generic dependency installation

Keep these concerns separate:

```text
install_offline.ps1     -> Python/runtime installation
install-service.ps1     -> Windows Service registration
```

This makes rollback and troubleshooting safer.

## C3. Installation order on a fresh target

Required documented order:

```text
1. Extract offline bundle
2. Run install_offline.ps1
3. Create/edit config.toml
4. Configure service secrets/account
5. Run deploy/windows/install-service.ps1
6. Verify localhost HTTP backend
7. Configure IIS HTTPS
8. Run external HTTPS smoke tests
```

### C acceptance criteria

- [ ] Fresh offline deployment does not need internet access.
- [ ] WinSW/service files are present in packaged artifact.
- [ ] Existing Python offline installation still works.

---

# Phase D — Configure production MCP HTTP settings

**Owner**: Coding/deployment agent

**Code change required**: Usually no; config change required

Production `config.toml` should use localhost backend binding.

```toml
[http]
host = "127.0.0.1"
port = 8080
bearer_token_env = "MCP_HTTP_BEARER_TOKEN"
```

## D1. allowed_hosts

IIS reverse-proxy Host behavior must be tested.

Preferred design: IIS preserves the external host header.

Then add the real external hostname:

```toml
allowed_hosts = [
    "127.0.0.1:*",
    "localhost:*",
    "<MCP_FQDN>:*",
]
```

Do not use an unrestricted wildcard merely to make a 421 error disappear.

## D2. allowed_origins

Non-browser MCP clients may not send `Origin`.

If a browser-based client is used, explicitly add its trusted origin. Do not add `*` by default.

## D3. Bearer token storage

The secret token must not be committed to GitHub or written into `config.toml`.

`config.toml` should only contain:

```toml
bearer_token_env = "MCP_HTTP_BEARER_TOKEN"
```

The actual value must be available to the Windows Service account/process.

For the first deployment, prefer a machine-level or service-specific secret setup rather than relying on the current interactive user's environment.

Generate a strong token using the project Python runtime:

```powershell
& ".\.venv\Scripts\python.exe" -c "import secrets; print(secrets.token_urlsafe(32))"
```

Do not print the production token into logs or commit it to scripts.

### D acceptance criteria

- [ ] MCP binds to `127.0.0.1:8080`.
- [ ] Remote machines cannot access port 8080 directly.
- [ ] Bearer token exists in the service runtime environment.
- [ ] `allowed_hosts` contains the actual FQDN used through IIS.

---

# Phase E — Install and configure IIS reverse proxy

**Owner**: Windows/IIS administrator

**Code change required**: No

## E1. Required IIS capabilities

Required components typically include:

- IIS Web Server
- URL Rewrite module
- Application Request Routing (ARR)
- ARR Proxy enabled

Because the server may be offline, obtain approved offline installers/packages through the organization's software distribution process before starting.

Do not assume these components are already installed.

## E2. Enable ARR proxy

In IIS Manager:

```text
Server
  -> Application Request Routing Cache
  -> Server Proxy Settings
  -> Enable proxy
```

The exact UI may differ by ARR version.

## E3. Create MCP IIS site

Recommended design:

```text
Site name: MCPServer
Binding: https / 443 / <MCP_FQDN>
Certificate: corporate certificate for <MCP_FQDN>
```

Avoid binding production traffic to an arbitrary high HTTPS port unless company network policy requires it.

## E4. HTTPS binding

Bind the certificate from:

```text
Local Computer / Personal certificate store
```

Verify the certificate selected in IIS matches the intended FQDN and has a private key.

## E5. Reverse proxy `/mcp`

Target:

```text
https://<MCP_FQDN>/mcp
    -> http://127.0.0.1:8080/mcp
```

Ensure the rule preserves:

- HTTP method (`GET`, `POST`, etc.);
- query string;
- `Authorization` header;
- appropriate streaming behavior;
- external Host header if the selected transport security configuration expects it.

Do not enable IIS authentication modes that consume or replace the application's Bearer `Authorization` header unless the architecture is intentionally redesigned.

Recommended IIS site authentication for the MCP reverse proxy layer:

```text
Anonymous Authentication: Enabled
Windows Authentication: Disabled
Basic Authentication: Disabled
```

Application-level Bearer authentication remains enforced by MCP.

## E6. Proxy timeout

MCP tool calls can outlive short web defaults, especially database/history queries.

Set an IIS/ARR proxy timeout that is comfortably above the intended MCP request window. Do not use a tiny timeout that causes IIS to terminate legitimate long-running tool calls before MCP or database-specific timeouts can respond.

Do not remove Oracle's application-level `call_timeout`; reverse-proxy timeout and database call timeout solve different problems.

## E7. Firewall

External inbound target:

```text
TCP 443 -> IIS
```

Backend ports should remain local:

```text
8080 -> localhost only
8081 -> localhost only, if exports are enabled
```

Do not open 8080/8081 broadly merely because IIS cannot reach them; IIS on the same machine should reach localhost directly.

### E acceptance criteria

- [ ] IIS HTTPS binding is valid.
- [ ] Corporate certificate is presented to clients.
- [ ] `/mcp` proxies to localhost MCP.
- [ ] Authorization header reaches MCP unchanged.
- [ ] HTTP 401 is returned for missing/wrong Bearer token.
- [ ] Correct token reaches MCP successfully.

---

# Phase F — Move export downloads behind the same HTTPS endpoint

**Owner**: Coding agent + IIS administrator

**Code change required**: Yes if `serve_downloads = true`

Skip this phase only when export download serving is intentionally disabled.

## F1. Existing problem

Current `download_server.py` builds URLs similar to:

```text
http://<advertise_host>:8081/exports/<id>
```

Once IIS is the public endpoint, the desired URL is:

```text
https://<MCP_FQDN>/exports/<id>
```

The backend download server may still remain plain HTTP on localhost.

## F2. Add explicit external/public download base URL

Preferred configuration addition:

```toml
[export]
serve_downloads = true
public_base_url = "https://<MCP_FQDN>/exports"
download_host = "127.0.0.1"
download_port = 8081
```

Modify configuration parsing and URL generation so `register_file()` returns:

```text
{public_base_url}/{file_id}
```

Do not infer the external scheme/host from the backend bind address.

## F3. Backward compatibility

If existing users depend on `advertise_host` + `download_port`, preserve the old configuration path temporarily unless there is a deliberate breaking-change decision.

Recommended behavior:

1. If `public_base_url` exists, use it.
2. Otherwise use the current `http://advertise_host:download_port/exports` behavior.

Validate `public_base_url` at startup:

- `https://` required for production examples;
- no trailing slash normalization bugs;
- no embedded credentials;
- valid URL structure.

## F4. IIS `/exports` proxy rule

Target:

```text
https://<MCP_FQDN>/exports/*
    -> http://127.0.0.1:8081/exports/*
```

The existing unguessable, time-limited export ID remains the capability controlling access to that file.

If stricter export authentication is required later, design it separately; do not accidentally break current cross-machine handoff semantics while performing the HTTPS migration.

## F5. Tests

Add tests for:

- `public_base_url` configuration parsing;
- returned URL uses HTTPS base URL;
- path joining does not produce `//exports` or missing slash;
- fallback behavior remains correct if backward compatibility is retained.

### F acceptance criteria

- [ ] Export server binds to localhost only.
- [ ] Generated `download_url` uses `https://<MCP_FQDN>/exports/...`.
- [ ] Remote consumer can download through IIS.
- [ ] Direct LAN access to port 8081 is unnecessary.

---

# Phase G — Logging, recovery, and operational verification

**Owner**: Coding/deployment agent

## G1. Persistent logs

Configure:

```text
MCP_LOG_LEVEL=INFO
MCP_LOG_FILE=<LOG_DIRECTORY>\mcp-server.log
```

Verify:

- service account can write the directory;
- logs survive PowerShell logout;
- Bearer token is never logged;
- API keys / DB passwords are never logged.

## G2. Service restart test

Identify the Python process associated with the service and terminate it intentionally in a controlled maintenance window.

Expected:

```text
WinSW detects process exit
-> service recovery/restart
-> 127.0.0.1:8080 is listening again
```

## G3. Machine reboot test

Restart Windows.

After reboot, without interactive login:

```powershell
Get-Service MCPServer
Get-NetTCPConnection -LocalPort 8080 -State Listen
```

Expected service status:

```text
Running
```

Then test HTTPS externally.

## G4. Database connectivity regression

Run representative tools for every enabled production database type.

At minimum, where applicable:

```text
db_list_databases
db_query
gms_list_points
gms_realtime_values
gms_history_aggregate
```

Verify service identity changes did not remove network/database permissions.

## G5. Connection-pool shutdown regression

The repository currently closes database pools when server execution exits through the normal `finally` path.

Do not remove that behavior while adding Windows Service support.

Verify stop/restart operations do not leave stale database connections indefinitely.

### G acceptance criteria

- [ ] Persistent logs are generated.
- [ ] Server automatically recovers from process failure.
- [ ] Server automatically returns after Windows reboot.
- [ ] Database/GMS tools work under the service account.
- [ ] No obvious connection leak appears during repeated service restart tests.

---

# Phase H — End-to-end security and client tests

**Owner**: Deployment agent / MCP client owner

Test from a different machine, not only from localhost.

## H1. DNS

```powershell
Resolve-DnsName <MCP_FQDN>
```

Must resolve to the MCP server.

## H2. TLS certificate

From the client, access:

```text
https://<MCP_FQDN>/mcp
```

The TLS stack must trust the certificate without `verify=False`, insecure flags, or self-signed bypasses.

If validation fails, inspect:

- corporate Root CA installed on client;
- Intermediate CA installed/served correctly;
- SAN matches hostname;
- certificate validity date;
- system clock.

## H3. Authentication negative test

Without token:

```text
Expected: HTTP 401 Unauthorized
```

With incorrect token:

```text
Expected: HTTP 401 Unauthorized
```

## H4. Authentication positive test

With the same Bearer token configured for the MCP Service:

```text
Expected: MCP initialize/session request succeeds
```

Do not consider a simple browser GET to `/mcp` a complete MCP test; use an MCP-capable client or a protocol-valid request.

## H5. Host-header test

If a `421 Misdirected Request` appears:

1. inspect the Host received by MCP;
2. inspect IIS preserve-host behavior;
3. verify `allowed_hosts` contains the actual host + port form;
4. fix the exact allowlist.

Do not disable DNS rebinding protection as the default fix.

## H6. Export test

If exports enabled:

1. run a tool that returns a `download_url`;
2. confirm URL begins with `https://<MCP_FQDN>/exports/`;
3. download it from the consuming machine;
4. verify expiration behavior still works;
5. verify direct `http://server:8081/...` is not required.

### H acceptance criteria

- [ ] TLS verification succeeds from another machine.
- [ ] Missing/wrong token fails.
- [ ] Correct token works.
- [ ] MCP tools execute end-to-end.
- [ ] Export URL works over HTTPS if enabled.

---

# Phase I — Documentation and operator runbook

**Owner**: Documentation/deployment agent

Update repository documentation after implementation.

Required documents/sections:

```text
README.md
config.toml.example
deploy/windows/README.md
docs/HANDOFF.md
```

## I1. README deployment section

Document the supported production topology:

```text
Corporate HTTPS -> IIS -> localhost MCP Windows Service
```

Make clear that direct `python -m ...` remains useful for development/debugging but is not the intended production lifecycle.

## I2. config.toml.example

If Phase F is implemented, document:

```toml
public_base_url = "https://mcp.internal.example.com/exports"
```

Keep secrets out of the example.

## I3. Windows Service runbook

Must include exact commands for:

```powershell
Get-Service MCPServer
Start-Service MCPServer
Stop-Service MCPServer
Restart-Service MCPServer
```

Also document:

- log location;
- config location;
- service account;
- how to update code;
- when `install_offline.ps1` must be rerun;
- how to rotate Bearer token;
- how to replace/renew IIS certificate;
- how to uninstall the service.

## I4. Upgrade procedure

Target operational upgrade sequence:

```text
1. Stop MCPServer service
2. Backup config.toml and deployment-specific secrets/settings
3. Update repository / extract new offline bundle
4. If dependencies changed, rerun install_offline.ps1
5. Start MCPServer service
6. Check logs
7. Run localhost smoke test
8. Run HTTPS smoke test
```

For a code-only update with unchanged dependencies, preserve the existing design goal that a full environment rebuild should not be required.

### I acceptance criteria

- [ ] A different maintainer can deploy without original author present.
- [ ] Production start/stop/restart procedure is documented.
- [ ] Certificate renewal procedure is documented.
- [ ] Bearer token rotation procedure is documented.

---

# 4. Recommended implementation order

Agents should execute in this order unless a concrete dependency requires otherwise.

```text
1. Phase A — collect FQDN / certificate / service account values
2. Phase B — Windows Service support
3. Phase C — offline packaging integration
4. Phase D — localhost production configuration
5. Verify MCP works as a service over local HTTP
6. Phase E — IIS + corporate HTTPS
7. Verify /mcp over HTTPS + Bearer token
8. Phase F — HTTPS exports, if enabled
9. Phase G — recovery/reboot/database regressions
10. Phase H — external client security tests
11. Phase I — final docs/runbook updates
```

Important sequencing rule:

> Do not debug IIS, TLS, Windows Service lifecycle, application authentication, and database permissions all at the same time. First prove the application works as a localhost Windows Service; then introduce IIS/TLS; then test remote clients.

---

# 5. Agent task boundaries

This section exists to prevent scope drift across multiple AI agents.

## Agent 1 — Windows Service implementation

Allowed scope:

```text
deploy/windows/**
scripts/pack_offline.ps1
scripts/install_offline.ps1
README deployment references if required
```

Must not redesign MCP tools or database behavior.

Deliverables:

- WinSW service definition;
- install/uninstall/start/stop/restart scripts;
- offline package integration;
- service lifecycle tests.

## Agent 2 — IIS / corporate certificate deployment guide

Allowed scope:

```text
deploy/windows/README.md
docs/** deployment sections
config.toml.example comments
```

Primary work may be infrastructure configuration rather than Python code.

Deliverables:

- exact IIS reverse-proxy configuration;
- HTTPS binding instructions;
- Host/Authorization behavior verification;
- firewall rules;
- acceptance-test record.

## Agent 3 — HTTPS export URL implementation

Allowed scope:

```text
src/mcp_server/config.py
src/mcp_server/utils/download_server.py
config.toml.example
tests/** related export/config tests
```

Must preserve export token/expiry semantics.

Deliverables:

- `public_base_url` or equivalent explicit public URL configuration;
- backward compatibility decision documented;
- tests;
- HTTPS-generated download URL.

## Agent 4 — End-to-end validation and documentation

Allowed scope:

```text
tests/**
README.md
docs/HANDOFF.md
deploy/windows/README.md
```

Deliverables:

- test matrix results;
- reboot/recovery verification;
- remote client verification;
- final operator runbook.

---

# 6. Agent handoff protocol

Every agent working on this plan must update the status block below before handing off.

## Current status

Use exactly one status per phase:

```text
NOT_STARTED
IN_PROGRESS
BLOCKED
CODE_COMPLETE
VERIFIED
```

Current initial state:

| Phase | Status | Owner/Agent | Evidence / Notes |
|---|---|---|---|
| A Infrastructure values | NOT_STARTED | — | Need real FQDN/certificate/service-account values |
| B Windows Service | NOT_STARTED | — | Repo currently has Linux systemd deployment only |
| C Offline packaging | NOT_STARTED | — | Must package Windows service wrapper offline |
| D Production HTTP config | NOT_STARTED | — | Final values depend on FQDN/IIS behavior |
| E IIS + corporate HTTPS | NOT_STARTED | — | Corporate certificate available per maintainer; details not yet recorded |
| F HTTPS exports | NOT_STARTED | — | Required only if `serve_downloads=true` |
| G Operations/recovery | NOT_STARTED | — | Requires service implementation |
| H End-to-end client tests | NOT_STARTED | — | Requires IIS + client trust |
| I Final runbook/docs | NOT_STARTED | — | Complete after implementation is verified |

## Handoff entry template

Append a dated entry at the bottom of this file for every agent handoff:

```markdown
## Handoff YYYY-MM-DD — <agent/name>

### Completed
- ...

### Changed files
- `path/to/file`

### Validation performed
- command / result

### Remaining work
- ...

### Blockers
- None / exact blocker

### Important decisions
- ...

### Next recommended action
- ...
```

Do not write vague entries such as "service updated". Include concrete file names, commands, results, and blockers.

---

# 7. Security constraints

The following are explicit guardrails.

- Never commit corporate certificate private keys.
- Never commit `.pfx` files containing private keys.
- Never commit the production Bearer token.
- Never write the Bearer token into normal application logs.
- Never disable TLS certificate verification as the production solution.
- Never replace the application Bearer token with "HTTPS only".
- Never expose backend `8080` broadly after IIS is active.
- Never expose export `8081` broadly after IIS is active.
- Never solve `421 Misdirected Request` by globally disabling DNS rebinding protection without explicit approval.
- Never run the service under an over-privileged admin/domain account just to avoid permission troubleshooting.
- Keep database credentials server-side and preserve existing alias-based configuration behavior.
- Preserve Oracle read-only protections and database connection-pool controls.

---

# 8. Rollback plan

If IIS or Windows Service deployment fails, rollback should be controlled and reversible.

## Windows Service rollback

```text
1. Stop MCPServer
2. Uninstall MCPServer service
3. Do not delete config.toml or logs automatically
4. Run MCP manually from PowerShell for diagnostic fallback
```

Manual fallback:

```powershell
Set-Location <PROJECT_ROOT>
& ".\.venv\Scripts\python.exe" -m mcp_server.server --transport streamable-http
```

Manual fallback is for troubleshooting only; it is not the completed production state.

## IIS rollback

```text
1. Disable/remove MCP reverse proxy rule
2. Preserve certificate in Windows certificate store
3. Keep backend bound to localhost if possible
4. Diagnose locally before re-exposing traffic
```

Do not reopen backend port 8080 to the full network as the default rollback.

---

# 9. Troubleshooting matrix

| Symptom | Likely area | First checks |
|---|---|---|
| PowerShell close stops service | Windows Service not implemented | `Get-Service MCPServer` |
| Service immediately stops | working directory/env/config | WinSW log, `MCP_CONFIG`, Python path |
| MCP works manually but not as service | service account permissions/env | DB/network/file permissions, Bearer env |
| HTTPS certificate warning | CA/SAN/chain | client Root CA, SAN, expiry |
| HTTP 401 through IIS | Bearer token/header | Authorization header forwarding, service env |
| HTTP 421 | allowed_hosts / Host | IIS preserve host + `[http].allowed_hosts` |
| HTTP 502 from IIS | backend unavailable | service status, localhost:8080 listener |
| Request dies during long query | IIS/ARR timeout | proxy timeout plus application/DB timeout |
| Export URL still says `http://...:8081` | export URL generator | Phase F `public_base_url` |
| Export URL gives 404 | IIS route / expired id | `/exports` rewrite + TTL |
| DB works interactively but fails as service | service identity | service account network/DB rights |
| PostgreSQL/Oracle sessions remain after stop | lifecycle cleanup | service shutdown path, pool close logs |

---

# 10. Final validation record

Do not mark the plan complete until this table is filled with real results.

| Test | Expected | Actual | Pass/Fail | Date |
|---|---|---|---|---|
| Windows Service installed | `MCPServer` exists | — | — | — |
| PowerShell closed | MCP remains running | — | — | — |
| Backend listener | `127.0.0.1:8080` | — | — | — |
| Reboot recovery | Service auto-starts | — | — | — |
| Corporate TLS | trusted + SAN match | — | — | — |
| HTTPS `/mcp` | reaches MCP | — | — | — |
| Missing Bearer | 401 | — | — | — |
| Wrong Bearer | 401 | — | — | — |
| Correct Bearer | MCP succeeds | — | — | — |
| 8080 remote access | unavailable | — | — | — |
| Export HTTPS | valid, if enabled | — | — | — |
| DB smoke test | succeeds | — | — | — |
| GMS smoke test | succeeds | — | — | — |
| Crash recovery | automatic restart | — | — | — |
| Connection cleanup | no obvious leaked pool sessions | — | — | — |

---

# 11. Immediate next action

The first implementation agent should start with **Phase A + Phase B**, not IIS.

Reason: before introducing TLS/reverse-proxy variables, the MCP application must first prove that it can run reliably as a non-interactive Windows Service with the correct working directory, environment variables, database permissions, logs, and restart behavior.

The first concrete repository change should therefore be:

```text
Add deploy/windows/ WinSW-based service deployment
+ integrate required service assets into offline packaging
```

After localhost service operation is verified, proceed to IIS/corporate HTTPS.

---

# Handoff log

## Handoff 2026-08-18 — Initial deployment plan

### Completed
- Audited current MCP HTTP runtime, HTTP configuration, Linux deployment files, and export download implementation.
- Chosen target production topology: corporate HTTPS certificate on IIS, reverse proxy to localhost MCP, MCP run as Windows Service.
- Defined separate HTTPS handling for cross-machine export URLs.
- Defined multi-agent task boundaries, acceptance criteria, rollback, and validation matrix.

### Changed files
- `docs/WINDOWS_HTTPS_DEPLOYMENT_PLAN.md`

### Validation performed
- Repository structure and relevant source files inspected on `main`.
- No production server changes have been made yet.

### Remaining work
- All implementation phases A–I.

### Blockers
- Real production FQDN, certificate thumbprint/details, service account, and project path have not yet been recorded.

### Important decisions
- IIS owns corporate TLS certificate.
- Uvicorn remains local HTTP only.
- Bearer authentication remains enabled.
- Windows Service is required; interactive PowerShell is not the production lifecycle.
- Export URLs must also migrate to externally advertised HTTPS if export serving is enabled.

### Next recommended action
- Implement Phase B Windows Service support and Phase C offline packaging integration, then validate localhost service operation before configuring IIS.
