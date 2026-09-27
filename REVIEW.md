# Multireview — Round 7 Consolidated Review

- **Snapshot:** `f139d85` (round-6 fixes) — branch `main`
- **Panel (roster completo, sin sustituciones):** GPT-5.6 Terra · GLM 5.3 (zai) · DeepSeek V4.1 Flash (openrouter)
- **Raw → consolidado:** 13 hallazgos en bruto → 9 únicos (**3 Medium, 6 Low, 0 High**)
- **Verificación de fixes r6 (f139d85):** sin regresiones encontradas. GLM 5.3: "the r6 fence handling accepts fully-fenced valid artifacts, rejects fenced/truncated markers, marker-only stray lines, and template-placeholder parroting… the two-poll absent/stall gates, cross-round fingerprint reset, empty-listing-as-absence, bounded error policies, and exit-code mapping all behave as documented and are well covered by the regression suite."

**Veredicto: LIMPIA (sin High).** Los hallazgos convergen en un tema: el watchdog puede depender del deadline backstop en vez de las rutas rápidas cuando hay condiciones de borde (CLI caído al arranque, timeout del CLI no whitelisted, agentes mixtos sanos/caídos).

---

## Medium

### M1 — Query failure de un agente contamina agentes sanos (1/3: Terra)
`scripts/review-babysitter.py:665`
El bucle de agentes hace `break` en el primer error y descarta los resultados ya recolectados de agentes consultados con éxito, enrutando todo el panel al fallback query-failed. Ese fallback puede promover a `done` un reviewer **activo** cuyo agente seguía respondiendo, solo porque el query de *otro* agente falló dos veces.
**Fix:** preservar resultados por agente; aplicar el artifact-aware fallback solo a las entries del agente cuyo query falló; clasificar normalmente las del resto.
**Trace:** modelos A (`agent:alpha:…`) y B (`agent:beta:…`); alpha responde A=running con artifact válido sin cambios; beta falla → sentinel reemplaza el merge → `evaluate_round(…, query_failed=True)` para ambos → segundo poll fallido de beta promueve A a `done` aunque alpha seguía reportándolo running.

### M2 — El fallback artifact-aware nunca terminaliza modelos `unknown` (1/3: GLM)
`scripts/review-babysitter.py:379` · SKILL.md:578
La promoción exige `old_status in TERMINAL_MODEL_STATES | {"active","hung"}`. Un modelo cuyo status nunca fue observado (`unknown`: primer poll del round, o CLI ya caído al arrancar el babysitter) se mantiene `unknown` para siempre; la ronda cabalga hasta `deadlineMs` y despierta como `deadline` con revisores "unresolved" pese a artifacts válidos y estables en disco. SKILL.md promete más de lo que el código hace (los tests fb3/fb4 lo confirman como comportamiento actual).
**Fix:** admitir `unknown` en la promoción cuando el artifact es válido y estable en 2 polls consecutivos de query fallida (el artifact es la evidencia), o corregir SKILL.md:578 para acotar la garantía.

### M3 — `"timeout"` ausente de `KNOWN_TERMINAL_STATUSES` (2/3: DeepSeek, GLM)
`scripts/review-babysitter.py:58` · SKILL.md:547
El CLI de OpenClaw emite `timeout` como status real (observado en vivo: `Counter({'killed':300,'done':135,'failed':49,'running':7,None:6,'timeout':1})`). No está en la whitelist → un reviewer cuyo turno expiró (falla esperada que este watchdog existe para detectar) clasifica `unknown` incluso con artifact válido; la ronda nunca llega a `complete-with-losses` y solo despierta por deadline. SKILL.md documenta la misma lista incompleta.
**Fix:** añadir `"timeout"` (y verificar el vocabulario completo de `openclaw sessions --json`: p. ej. `stopped`/`expired`) a la whitelist y a SKILL.md:547.

## Low

### L1 — Resolución de `sessionFile` relativa al CWD (1/3: GLM)
`scripts/review-babysitter.py:125`
Bajo `systemd-run --user` (CWD=$HOME), un `sessionFile` relativo resuelve contra `$HOME` → `stat()` falla siempre → `transcriptObservation` siempre None → `growing` nunca true → `stalled-with-artifact` inalcanzable; una sesión trabada degrada a `hung` hasta deadline. Afecta también a `last_assistant_text` (:236).
**Fix:** resolver relativos contra `sessions_dir_for(agent_id_for(...))` y registrar contador de fallos de observación de transcript.

### L2 — Env vars del babysitter no documentados (1/3: GLM)
`SKILL.md:591`
`BABYSITTER_POLL_INTERVAL`, `BABYSITTER_STALL_THRESHOLD`, `OPENCLAW_AGENT_ID`, `OPENCLAW_SESSIONS_DIR` (+`OPENCLAW_BIN`) no aparecen en SKILL.md ni README. Un operador no puede descubrir los knobs, y la unidad systemd-run de ejemplo no los recibe sin editar la skill.
**Fix:** nota "Babysitter environment" en SKILL.md con defaults/mínimos y `--setenv=` en el ejemplo.

### L3 — Placeholder case-broken para `N/A` (1/3: DeepSeek)
`scripts/review-babysitter.py:54,147`
`_block_valid` compara `value.lower() in TEMPLATE_PLACEHOLDERS`, pero `<concrete trace for logic claims, or N/A>`.lower() termina en `n/a` y no matchea → un bloque con **solo** la línea de trace parroteada pasa como válido. El test r6 L1 no lo pilla porque también parrotea `reasoning` (todo minúsculas).
**Fix:** pre-lowercasear el set (o normalizar ambos lados) + test con solo `trace` parroteado.

### L4 — `NO FINDINGS` totalmente fenceado rechazado (1/3: DeepSeek)
`scripts/review-babysitter.py:172-180`
El fix r6 L4 acepta un findings file completo dentro de un fence, pero si el fence envuelve `NO FINDINGS`, `stripped` queda vacío y se devuelve `invalid` sin probar el sentinel. Inconsistente: mismo wrapping, veredicto opuesto según haya bloques o no.
**Fix:** en la rama `if not stripped:`, aceptar también el sentinel fenceado.

### L5 — `write_errors` sin threshold en la rama query-failed (1/3: DeepSeek)
`scripts/review-babysitter.py:685`
`write_errors` se incrementa en la rama session-query-failure pero el chequeo `>= MAX_CONSECUTIVE_ERRORS` solo existe en el path normal (:705). Runtime no escribible + CLI caído → el watchdog acumula errores sin disparar `monitor-error` hasta el deadline, contradiciendo el guarded path "runtime state write" de SKILL.md.
**Fix:** aplicar el mismo chequeo de threshold tras incrementar en ambas ramas (query-failed y config-error).

### L6 — Lock acquisition fuera de la política de errores acotados (1/3: DeepSeek)
`scripts/review-babysitter.py:540`
`lock_path.open("w")` + `fcntl.flock` corren antes del loop guardado; un OSError (EACCES, ENOSPC, ENOTDIR) escapa como traceback y `Restart=on-failure` produce un restart-loop infinito sin wake ni diagnóstico persistido — el escenario que las guarded failure paths pretenden evitar.
**Fix:** envolver el setup del lock en su propio try/except que pase por `_terminal` (o exit 0 + log para condición claramente no progresable).

---

## Verificación de fixes r6 → estado

| Fix r6 | Veredicto r7 |
|---|---|
| H1 stale-replay hang | FIXED (sin nuevos hallazgos) |
| M1 fingerprint cross-round | FIXED |
| M2 deadlineMs finito | FIXED |
| M3 _env_int bounds | FIXED |
| M4 sessionKey shape | FIXED |
| M5 artifact-aware fallback | FIXED pero ver M2 (contrato vs código) y M1 (scope por agente) |
| M6 lost/unknown contract | FIXED pero ver M3 (vocabulario incompleto) |
| M7 session errors non-terminal | FIXED pero ver M1 (contaminación entre agentes) y L5 |
| L1 placeholders | FIXED parcialmente — ver L3 (case) |
| L2 empty listing | FIXED |
| L3 exit-0 docs | FIXED |
| L4 fenced artifacts | FIXED parcialmente — ver L4 (sentinel fenceado) |
| L5 marker whitespace | FIXED |

---

*Consolidación: orquestador (panel declarado arriba). La concordancia del orquestador no incrementa N/M.*
