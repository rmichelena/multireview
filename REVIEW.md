# Multi-Review — rmichelena/multireview (Ronda 2, fresh-eyes)

- **Panel:** openai/gpt-5.6-terra · zai/glm-5.3 · openrouter/deepseek/deepseek-v4.1-flash (3/3 respondieron)
- **Target:** `main@0765a45` (tras remediación de la ronda 1)
- **Fecha:** 2026-09-23 22:06 CET
- **Método:** revisión fresh-eyes de repositorio completo; los revisores no vieron REVIEW.md de la ronda 1.

**Resultado: 1 High · 8 Medium · 6 Low.** El happy path del watchdog es sólido (artefactos, lock, escrituras atómicas, wake); los defectos se concentran en las rutas de error y ciclo de vida.

---

## High

### H1. La ruta de errores de config termina sin despertar al orquestador → bucle infinito de reinicios silencioso (3/3 reviewers: gpt-5.6-terra, glm-5.3, deepseek-v4.1-flash)
- `scripts/review-babysitter.py:~268` — `config_errors >= 3` fija `round=monitor-error` y hace `return 1` **sin llamar `wake_orchestrator`** (a diferencia de la ruta de errores de sesión, que sí despierta). Con `Restart=on-failure` + `RestartSec=30`, systemd relanza el proceso, que reinicia contadores y repite el ciclo indefinidamente: la review queda stranded para siempre si `pending-state.json` se corrompe/borra.
- **Fix:** despertar al orquestador en la ruta config-error (persistiendo `orchestratorSessionKey` fuera del config o usando el último config válido); tratar config/reviewDir ausentes como parada limpia (exit 0); añadir límite de reinicios (`StartLimitIntervalSec`/`RuntimeMaxSec`). Test que fuerza 3 fallos de config y assertion de wake.

## Medium

### M1. Excepciones no guardadas en `evaluate_round` → crash-restart loop sin estado ni wake (1/3 reviewers: glm-5.3)
- `validate_config` solo verifica truthiness: un `"deadlineMs": "2026-09-24T00:00:00Z"` (string ISO) pasa y `current >= config["deadlineMs"]` lanza `TypeError` no capturado → exit 1 sin runtime persistido → systemd reinicia y crashea igual para siempre.
- **Fix:** validar tipos (deadlineMs epoch-ms numérico, models lista de dicts) y/o envolver evaluate+wake en la política de errores acotados.

### M2. Sesión ausente + artefacto válido = `done` instantáneo, sin chequeo de estabilidad (1/3 reviewers: glm-5.3)
- `classify()` con `session is None` devuelve `done` si el artefacto es válido, sin la estabilidad de dos polls que el propio script exige para sesiones stale. Un sessionKey erróneo + fichero escrito en dos pasadas → consolidación de un fichero truncado.
- **Fix:** exigir la misma estabilidad de dos observaciones para sesiones ausentes; pasar `--agent`/`--all-agents` a `sessions --json` para que el lookup matchee.

### M3. El validador rechaza "NO FINDINGS" + summary que los propios templates invitan a escribir (1/3 reviewers: glm-5.3)
- `artifact_state()` exige exactamente `NO FINDINGS`, pero los templates dicen "End the file with a short quality summary" incondicionalmente. Un reviewer con 0 findings que añade summary se clasifica `lost` (pérdida fabricada). A la inversa, texto arbitrario alrededor de bloques completos sí se acepta, contradiciendo el contrato documentado.
- **Fix:** relajar el validador (línea inicial `NO FINDINGS` sin bloques = válido) o hacer los templates inequívocos; alinear la regla del remainder con SKILL.md.

### M4. `stalled-with-artifact` puede dar por terminado un reviewer aún activo con un solo poll stale (1/3 reviewers: deepseek-v4.1-flash)
- La observación se compara contra el poll previo sin exigir que ambos polls sean stale; un reviewer silencioso >900s dentro de una tool call larga (con draft ya escrito que piensa revisar) se marca terminal y se consolida a medias. Nota: el timeout default de subagentes es 0 (sin límite), así que las corridas largas no están acotadas.
- **Fix:** exigir N≥2 polls stale consecutivos con artefacto inmutable, o mtime del artefacto > STALL_THRESHOLD; no equiparar a `done` mientras `status == running`.

### M5. `lastInteractionAt` ausente/0 → sesión `running` queda `active` para siempre, solo termina en deadline (1/3 reviewers: deepseek-v4.1-flash)
- `stale = bool(last_activity and ...)` cortocircuita a False sin timestamp; verificado que el store local contiene entradas reales sin `lastInteractionAt`. Un reviewer terminado cuya entrada laggea bloquea la ronda hasta el deadline completo.
- **Fix:** fallback a `updatedAt`/`sessionStartedAt`/mtime del artefacto; con artefacto válido e inmutable y sin timestamp, tratar como `stalled-with-artifact`.

### M6. Errores de E/S de estado (`save_json`) sin guardar → crash del watchdog fuera de la política acotada (1/3 reviewers: deepseek-v4.1-flash)
- `save_json` se llama sin try/except en ~7 sitios; disco lleno, chmod 0555 o `os.replace` fallando (EXDEV/ENOSPC) propagan la excepción, exit 1, reinicio infinito sin `round` ni wake — contradiciendo el `monitor-error` documentado para "state-I/O failures".
- **Fix:** envolver las escrituras en el contador de errores acotados con transición a `monitor-error` + wake.

### M7. `query_sessions` descarta sesiones sin `key` y colapsa duplicados; pérdidas se reportan como `deadline` genérico (1/3 reviewers: deepseek-v4.1-flash)
- Cualquier mismatch de `sessionKey` resuelve `None` → `unknown` (con artefacto ausente) → la ronda solo muere en deadline y `lostModels` nunca se emite, privando al operador del diagnóstico exacto. Además `--limit all` vuelca el store completo (~432 entradas) cada 300s.
- **Fix:** diagnosticar keys ausentes del config ("not yet spawned" vs "unknown key"), considerar filtrado por agente o query acotada.

### M8. `validate_config` no rechaza `file`/`sessionKey` duplicados → "complete" falso y atribución inflada (1/3 reviewers: deepseek-v4.1-flash)
- Dos entradas apuntando al mismo `file` hacen que la segunda sea `done` con el artefacto de la primera, incluso sin haber producido nada: `complete` con `lostCount` 0 y `(N/M reviewers)` sobrecontando soporte independiente.
- **Fix:** assert de unicidad de `file` y `sessionKey` en `validate_config`.

### M9. Tests solo cubren el happy path; todas las rutas con defectos reales están sin asertar (2/3 reviewers: glm-5.3, deepseek-v4.1-flash)
- Sin cobertura de: rutas monitor-error (config y sesión), reintentos de wake fallidos, deadline con hung, lock contention, sesión ausente+valido, `main()` end-to-end. Cada finding anterior sería atrapado por un unit test directo.
- **Fix:** tests con fake `OPENCLAW_BIN` cubriendo config corrupto, wake rc!=0 ×3, errores de query ×3, segunda instancia, stalled-with-artifact → complete, y casos `NO FINDINGS`+summary y CRLF en `artifact_state`.

## Low

### L1. `last_assistant_text` relee el transcript completo en cada poll y traga todos los errores (2/3 reviewers: glm-5.3, deepseek-v4.1-flash)
- Para un modelo `lost`, relee y parsea el .jsonl completo (decenas de MB) cada 300s mientras otros reviewers corren; los fallos colapsan a `""` sin distinción.
- **Fix:** leer solo la cola (últimos ~256KB), cachear por sessionKey, registrar `unreadable: <reason>`.

### L2. `FINDING_RE` es frágil a CRLF/BOM → review completo clasificado `lost` (1/3 reviewers: glm-5.3)
- `===FINDING===\r\n...` no matchea; `\ufeffNO FINDINGS` no iguala el sentinel.
- **Fix:** normalizar (`\r\n`→`\n`, lstrip BOM) antes de validar.

### L3. El patrón de publicación escribe a `/tmp/<review>.md`, contradiciendo la regla workspace-only del propio skill (1/3 reviewers: glm-5.3)
- Las file tools sandboxeadas no pueden leer/escribir `/tmp`; el patrón de publicación debería usar el review dir.
- **Fix:** `$BASE/REVIEW-CONSOLIDATED.md` con `/tmp` solo como fallback explícito no-sandboxed.

### L4. Runtime terminal conserva `configErrors`/`lastConfigError` stale tras recuperarse (1/3 reviewers: glm-5.3)
- Solo se resetea el contador local; el runtime persistido arrastra el error anterior y induce falsas conclusiones de "monitor con problemas" en el reporte final. La familia de errores de sesión sí se resetea: asimetría.
- **Fix:** `runtime.pop("configErrors"/"lastConfigError")` al cargar config OK.

### L5. Watchdog Linux/systemd-only y dependiente de PATH, vs. claim agent-agnostic del README (1/3 reviewers: deepseek-v4.1-flash)
- `fcntl` es POSIX-only, `systemd-run --user` no existe en macOS, y bajo systemd el PATH puede no tener `python3`/`openclaw`.
- **Fix:** documentar requisito Linux+systemd explícitamente, resolver binarios con `sys.executable`/`shutil.which`, marcar el babysitter como opcional en hosts no-systemd.

### L6. La sección de debugging referencia scripts del workspace no incluidos en el repo (1/3 reviewers: deepseek-v4.1-flash)
- `sessions-map.py`/`subagent-view.py` viven solo en el workspace del operador; un adoptante nuevo que "copie el repo" falla al seguirlos.
- **Fix:** shipearlos bajo `scripts/` o reemplazar la sección por comandos `openclaw sessions --json` self-contained.

---

## Nota operativa de esta ronda

- El wake explícito vía `systemd-run` no llegó a ejercitarse de extremo a extremo: la unidad ya no estaba cargada al momento terminal y la consolidación se disparó por los completion events de los subagentes (los tres artefactos estaban válidos y las sesiones terminales). Esto es consistente con el clúster de findings H1/M1/M6: las rutas de fallo del watchdog siguen siendo su punto débil.
- Prioridad de fix sugerida: H1 → M1/M6 (crash/restart loops) → M2/M4/M5 (lógica de terminalidad) → M3 (validador vs templates) → M7/M8 (validación de config) → M9 (tests) → Lows.

## Remediación ronda 1 (verificación del orquestador)

Los 12 findings de la ronda 1 (`e198f08`) fueron corregidos en `0765a45` y esta ronda fresh-eyes no re-reportó ninguno de ellos como abierto: killed→lost ✔, validación estructural de artefactos ✔ (aunque M3 encuentra una colisión nueva con los templates), separación config/runtime ✔, lock+atomicidad ✔, wake explícito ✔ (diseñado; ver nota operativa), atribución (N/M) ✔ aplicada en este documento.

---

# Multireview — Ronda 3 (fresh-eyes, post-fixes)

> Roster: openai/gpt-5.6-terra · zai/glm-5.3 · openrouter/deepseek/deepseek-v4.1-flash (3/3 — sin sustituciones)
> Target: contenido de `26523be` · Fecha: 2026-09-23 · Raw: 14 findings → 10 únicos tras dedup

**Tally: 2 High · 4 Medium · 4 Low**

## High

### H1. Watchdog consulta solo el agente por defecto — reviews de otro agente son invisibles (2/3 reviewers: openai/gpt-5.6-terra, openrouter/deepseek/deepseek-v4.1-flash)
`scripts/review-babysitter.py:89` — `query_sessions()` corre `openclaw sessions --json --limit all` sin `--agent`; con un orquestador no-default, los session keys de los revisores nunca aparecen: revisor caído sin artefacto queda `unknown` hasta deadline en vez de `lost`. Fix: derivar/declarar `agentId` desde `orchestratorSessionKey` y pasarlo a la query y al fallback de transcript; test de regresión con agente no-default.

### H2. `internal_errors` nunca se resetea — "3 consecutivos" es en realidad "3 totales" (2/3 reviewers: zai/glm-5.3, openrouter/deepseek/deepseek-v4.1-flash)
`scripts/review-babysitter.py:401` — los otros contadores se resetean en cada ciclo exitoso, pero `internal_errors` es acumulativo de por vida. Tres fallos transitorios separados por horas (p.ej. el race del stat, M2) matan una sesión sana con un falso `monitor-error` + wake. Contradice docstring y SKILL.md ("bounded consecutive"). Fix: resetear al final de cada poll exitoso + test.

## Medium

### M1. Validator acepta bloques vacíos y `NO FINDINGS` + texto arbitrario como artefacto válido (1/3 reviewers: openai/gpt-5.6-terra)
`scripts/review-babysitter.py:72` — `===FINDING===\n\n===END_FINDING===` pasa como válido; revisor caído a mitad de escritura cuenta como `done` y el panel cierra "completo". Fix: exigir campos obligatorios no vacíos por bloque (severity/title/file/line/reasoning/fix/trace) y sentinela estricto para cero findings.

### M2. `path.stat()` TOCTOU fuera de handler — FileNotFoundError alimenta `internal_errors` (1/3 reviewers: zai/glm-5.3)
`scripts/review-babysitter.py:62` — `is_file()` y `stat()` son llamadas separadas y solo `read_bytes` está protegida; el unlink entre medias (el revisor reescribiendo su artefacto) propaga excepción al guard externo. Amplifica H2. Fix: envolver toda la observación en `try/except OSError → ("missing", None)`.

### M3. Validator rechaza resúmenes válidos que mencionan el token `===FINDING===` (1/3 reviewers: openrouter/deepseek/deepseek-v4.1-flash)
`scripts/review-babysitter.py:75` — el chequeo de "stray markers" es substring sobre el remainder, que incluye el quality summary permitido. Un resumen que parafrasee el formato (p.ej. "wrote 1 ===FINDING=== block") marca el artefacto `invalid` → revisor `lost` → falsos `complete-with-losses`. Fix: buscar solo líneas marcador-exclusivas (`^\s*===(FINDING|END_FINDING)===\s*$`).

### M4. Config irrecuperable sin wake key → exit 1 → restart-loop silencioso de systemd (1/3 reviewers: openrouter/deepseek/deepseek-v4.1-flash)
`scripts/review-babysitter.py:373` — con config corrupta desde el arranque, `last_good_key=None` → wake no intenta nada → exit 1 → `Restart=on-failure` reinicia indefinidamente sin avisar a nadie, contradiciendo la garantía documentada. Fix: exit 0 (o no-restart) tras escribir `monitor-error` cuando no hay key, o presupuesto acotado de reinicios + log estridente.

## Low

### L1. Test H1b: override de `OPENCLAW_BIN` vía env es código muerto; el test puede invocar el CLI real (2/3 reviewers: zai/glm-5.3, openrouter/deepseek/deepseek-v4.1-flash)
`scripts/test-review-babysitter.py:205` — `OPENCLAW` se captura al importar; mutar `os.environ` después no hace nada. El rc==1 pasa por otra razón y el comentario miente. Fix: parchar `bs.OPENCLAW` directamente (restore en finally) y asertar que el subprocess falló.

### L2. Sentinela `__duplicate__` nunca se consulta; el último duplicado gana silenciosamente (1/3 reviewers: zai/glm-5.3)
`scripts/review-babysitter.py:100` — un key duplicado (reciclado) puede clasificarse contra el registro stale; además colisión posible con sesiones llamadas `__duplicate__`. Fix: enrutar duplicados al camino de session-error o preferir el registro con timestamp más nuevo.

### L3. SKILL.md: fence de código roto en la sección Debugging (2/3 reviewers: zai/glm-5.3, openrouter/deepseek/deepseek-v4.1-flash)
`SKILL.md:~586-592` — comandos `ls -la`/`cat` sin fence y un ``` huérfano que absorbe la prosa siguiente. Fix: re-envolver en ```bash y borrar el fence sobrante.

### L4. Doc/code divergence: SKILL.md define `monitor-error` solo como "3 fallos de session-query o state-I/O" (1/3 reviewers: zai/glm-5.3)
`SKILL.md:~556` — en el código también disparan monitor-error los config-errors, write-errors y excepciones internas; documentar las cuatro fuentes y los campos `last*Error` de diagnóstico.

## Quality summaries del panel
- **Terra:** suite no cubre lookup multi-agente ni validez a nivel de schema del artefacto.
- **GLM:** la state machine core (two-poll terminality, locking, wake retries) es sólida en happy paths; el issue de mayor impacto es H2 amplificado por M2.
- **DeepSeek:** el falso-negativo del validator (M3) es el más probable en la práctica — degrada silenciosamente un revisor bueno a `lost`. Verificó contra el CLI real que las claims del SKILL sobre flags de `sessions`/`system event` son correctas.

## Nota de historial
El commit de restauración reemplaza a `26523be` (contenido idéntico, blob a blob) tras un force-push accidental; los SHAs previos (`e198f08`, `0765a45`, `b9d8872`, `26523be`) siguen consultables vía GitHub API.

## Observación operativa
El babysitter corrió ~5 min vía `systemd-run --user` y llegó a terminal (`round=done`, 3/3 models done) sin intervención — primer ciclo detached completo. El wake explícito sigue sin observarse como entrega de evento (la consolidación fue disparada por completion events): pendiente capturar un wake real.
