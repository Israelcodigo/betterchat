# Diseño de Nexo Kit

Decisión previa a la construcción, 10 de octubre de 2026. Alcance inferido del contexto: un kit de skills y harness para desarrollo general en Windows con Claude Code y Codex CLI. Implementación original; no se concatenan los instaladores ni se redistribuyen los catálogos investigados.

La actualización 1.1 añade [selección automática y aprendizaje autorizado](AUTOMATIC-LEARNING.md): observaciones por petición, tres éxitos, candidata, caso nuevo y activación con hashes. El registro de aprendizaje usa `.nexo/learning.sqlite3`; no duplica tareas ni reemplaza su fuente canónica.

## Estado canónico de tareas y memoria

`<proyecto>/.nexo/state.sqlite3` mantiene tareas, dependencias, intentos, leases, eventos y memorias. SQLite WAL y transacciones de escritura serializan las reclamaciones. Cada reclamación recibe un token nuevo: un trabajador antiguo no puede terminar una reclamación recuperada por otro. El estado humano se exporta a Markdown; no constituye una segunda base de datos editable.

Estados: pending → running → review → done. Un proceso que termina con código cero produce una propuesta en review; los gates configurados deben ejecutarse sobre el código actual para completar la tarea. Fallos o bloqueos admiten reintentos explícitos y limitados. Una dependencia fallida no cuenta como completada.

## Ejecución y aislamiento

Un runner en primer plano ejecuta tareas desbloqueadas con un límite de concurrencia. Usa worktrees Git separados para trabajadores paralelos y persiste sus ubicaciones. Hay adaptadores de procesos para `codex exec` y `claude -p`, más un adaptador de comando explícito para pruebas o ejecutores propios. Los comandos se representan como listas de argumentos, sin concatenación de shell. Cada proceso tiene timeout, logs acotados en la respuesta y heartbeat de lease. No se añade un daemon ni tmux como requisito de Windows.

Los resultados permanecen en sus ramas/worktrees. Una integración explícita comprueba el destino, realiza el merge y repite los gates; no publica ni hace push. No se implementa consenso distribuido: este kit opera en una máquina y un repositorio.

## Evidencias y contexto

Los gates son comandos configurados por proyecto, con timeout, salida, exit code y, opcionalmente, JUnit. Un fingerprint de contenido identifica el estado verificado. Los gates vacíos, faltantes, fallidos o con evidencia obsoleta no autorizan done. Las revisiones del modelo son complementarias a las pruebas ejecutadas.

La salida extensa se guarda e indexa localmente; el agente recibe un extracto con identificador y puede buscar fragmentos. Esto reduce exposición de salida en el contexto; **no es un sandbox de seguridad del sistema operativo**. Se preserva el sandbox nativo del cliente.

## Memoria y aprendizaje

Memorias delimitadas al proyecto, recuperadas por FTS5/BM25 con fallback lexical. Las correcciones se registran como candidatos con fuente y confianza informativa. Su activación y exportación como skill son acciones explícitas; un patrón repetido no se convierte por sí solo en una regla global. Se conservan relaciones entre memorias y la evidencia original. No se requiere un servicio de embeddings ni un LLM adicional.

## Una distribución, dos adaptadores

`init` crea la configuración local y materializa runtime, skills, roles, hooks y MCP dentro del proyecto. La configuración existente se combina por clave, conservando las entradas ajenas; las instrucciones compartidas usan un bloque identificable. La instalación registra hashes y copias anteriores para permitir desinstalación conservadora. Hooks Claude/Codex se generan por separado según sus contratos documentados. El usuario conserva el mecanismo nativo de confianza de hooks.

## Selección y conflictos

| Función | Elección | Resolución |
|---|---|---|
| Flujo | Superpowers + Addy + GSD | Un único plan con criterios observables; fases profundas solo cuando aportan valor. |
| Coordinación | OMX + Beads + Agent Orchestrator | DAG y leases transaccionales locales; sin varios buses/daemons. |
| Verificación | proof-pack de Anthropic + prácticas de Trail of Bits | Evidencia calculada desde ejecución y contenido, no desde una afirmación del agente. |
| Memoria | Compound + ECC + planning-with-files | Candidatos revisables y artefactos persistentes con una fuente de verdad. |
| Contexto | context-mode + claude-mem | Almacenamiento y recuperación por capas original; sin copiar runtimes ni sus licencias. |
| Integración Git | Gas Town | Verificación posterior al merge, con integración explícita y sin publicar. |
| Portabilidad | Python stdlib + adaptadores de CLI | Windows nativo sin Bash/tmux, Python 3.11+, SQLite incluido. |

El inventario y las correcciones a las dos investigaciones se conservarán en `INVENTORY.md` y en `research/*.json`; las divergencias verificadas no se resolverán en silencio. El informe de verificación distinguirá tests deterministas, ejecución real de clientes y funciones no comprobadas.
