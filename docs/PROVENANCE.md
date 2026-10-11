# Procedencia y selección técnica de Nexo

Nexo es una implementación original. No reúne plugins instalados dentro de otro plugin ni copia sus runtimes. Se atribuyen técnicas y decisiones de diseño; las licencias y los límites de las fuentes están en [INVENTORY.md](INVENTORY.md).

## Características incluidas

| Característica del kit | Procedencia conceptual | Implementación y motivo de elección |
|---|---|---|
| Selección automática por intención | Skills nativas de Codex y preferencia explícita del usuario | Descripciones precisas y router global; lectura de las skills pertinentes a cada fase sin exigir invocación por nombre. |
| Procedimientos repetidos convertidos en skills (1.1) | Ciclos de aprendizaje de ECC, Compound y Hermes; solicitud explícita del usuario | Implementación original de Nexo: tres peticiones distintas verificadas, candidata escrita por el agente, caso nuevo, hashes de contenido y logs, instalación con control de propiedad y respaldo. La frecuencia no activa reglas arbitrarias. |
| Un plan con criterios observables | Addy, Superpowers, CCH, BMAD | nexo-plan registra resultados, límites, pruebas y dependencias en el contrato de cada tarea. Evita planes narrativos sin una condición de terminación. |
| Un estado canónico de proyecto | Beads, Agent Orchestrator, planning-with-files | SQLite para coordinación y vistas Markdown para personas; no dos trackers que puedan contradecirse. |
| Dependencias y trabajo listo | Beads, GSD Core | El scheduler selecciona tareas cuyos prerrequisitos terminaron; un fallo no se trata como éxito. Es más sencillo de comprobar que una prioridad inferida por prompts. |
| Propiedad de tareas recuperable | OMX, OMC, Beads | Claim transaccional, token, lease y heartbeat. Un trabajador obsoleto no puede finalizar una reclamación nueva. |
| Intentos y ejecución acotada | OMX, OMC, Hermes | Workers, ciclos y timeouts explícitos. Conserva diagnóstico al agotar un límite en lugar de repetir indefinidamente. |
| Adaptadores Claude Code y Codex CLI | OpenAI Codex, ecosistemas oficiales, Agent Orchestrator | Usa procesos de clientes existentes; separa la orquestación del proveedor y mantiene sus permisos nativos. |
| Worktree por trabajo paralelo | Superpowers, Gas Town, Agent Orchestrator | Aísla cambios Git por tarea. El paralelismo se admite cuando hay separación de archivos y resultados. No promete aislamiento de seguridad del SO. |
| Integración local verificada | Gas Town | Merge explícito y nuevos checks en destino. Se elige el principio de verificar el resultado integrado, sin incorporar su cola distribuida ni bisect automático. |
| Revisión independiente | Superpowers, Addy, CCH, Trail of Bits | nexo-review y rol reviewer empiezan por contrato/diff. Hallazgos incluyen disparador, consecuencia y evidencia; no son un veredicto infalible. |
| Tests de comportamiento | Matt Pocock, Trail of Bits, Superpowers | nexo-build/nexo-verify priorizan interfaces públicas y regresiones pertinentes. Se descartan tests que repitan literalmente la implementación. |
| Completitud por evidencia | proof-pack de Anthropic, CCH | Checks reales, exit code, logs y fingerprint del contenido/configuración. Se rechaza evidencia vacía u obsoleta y un exit code cero del trabajador no marca done. |
| Lectura de JUnit | proof-pack de Anthropic | Si se configura, se analiza el informe regenerado, sus casos y fallos. No se presume que toda suite produzca JUnit ni se acepta un archivo viejo. |
| Contexto recuperado por capas | claude-mem, context-mode, Hermes | Salida persistida, búsquedas compactas por ID y lectura del detalle necesario. No se finge un resumen semántico sin ejecutar un modelo. |
| Búsqueda local del conocimiento | agentmemory, claude-mem, deja-vu | FTS5/BM25 con fallback lexical; no servidor de embeddings, descarga de modelos ni servicio externo obligatorio. |
| Checkpoints para reanudar | planning-with-files, GSD, context-mode | nexo-resume reconstruye tareas/worktrees/evidencia y comprueba actualidad antes de continuar. No considera un resumen como prueba de completitud. |
| Correcciones como candidatos | ECC, Task Observer, pro-workflow | memory add conserva fuente y tipo. La confianza se trata como heurística, no certeza estadística ni autorización. |
| Aprobar y promover aprendizaje | Compound, Task Observer, Hermes | memory approve activa una memoria y memory promote genera una candidata de skill en .nexo/learned-skills. La candidata requiere validación e instalación separada; no se activa silenciosamente en los hosts. |
| Aplicabilidad y caducidad del conocimiento | Compound | La skill de memoria incluye condiciones, evidencia y motivo para revisar una nota. No se convierte una incidencia local en una regla global. |
| Roles pequeños y propósito claro | wshobson, VoltAgent, SuperClaude | Cuatro roles originales: planner, implementer, reviewer y tester. Evitan un catálogo de perfiles que compitan por la misma tarea. |
| Skills con carga progresiva | Anthropic skills, OpenAI plugins, Hermes | Nueve entradas específicas en 1.1; referencias solo para contratos y evidencia avanzada. Los assets son compartidos, los adaptadores de host permanecen separados. |
| Instalación local conservadora | Ecosistemas oficiales y decisión propia | Archivos de proyecto, combinación de configuración y registro de instalación. No modifica indiscriminadamente perfiles globales ni instala dependencias de los repos investigados. |
| Windows sin tmux ni Bash obligatorio | Limitaciones observadas de OMC/OMX/CCH; decisión propia | Python stdlib, argumentos separados y scripts/integraciones por host. El runtime no depende de una shell POSIX para coordinar tareas. |
| Autoridad y límites transparentes | Límites observados de CCH/context-mode; decisión propia | Se distingue check de calidad de permiso/sandbox. No se introduce un parser heurístico de shell como garantía universal de seguridad. |

## Resolución de solapamientos

**Planificación.** Addy y Superpowers aportan secuencia; GSD y Beads aportan dependencias. Nexo mantiene un solo contrato por tarea y un DAG durable. Los documentos visibles se derivan del estado: no hay un plan de un plugin, otro de un tracker y un tercero en memoria.

**Memoria.** Las observaciones de claude-mem son útiles para reconstruir hechos; ECC y Task Observer aportan candidatos; Compound filtra lo que merece preservarse. Se combinan registro y selección, dejando la promoción explícita. No se activa una regla porque aparezca muchas veces en un log.

**Contexto.** Se adopta búsqueda lexical y lectura por ID como base comprobable. Vectorización, grafos semánticos y resumen LLM son técnicas complementarias, pero no se anuncian como implementadas. El contenido recuperado no puede cambiar por sí mismo los permisos.

**Verificación.** Los revisores pueden descubrir defectos que un test no cubre; los checks aportan resultados reproducibles. Son complementarios. Nexo no calcula éxito por unanimidad de agentes ni convierte una opinión del modelo en prueba ejecutada.

**Orquestación.** Worktrees y leases resuelven problemas diferentes: separación de cambios y propiedad de trabajo. Se incorporan ambos. Se omiten topologías de enjambre, consenso distribuido y múltiples buses de eventos porque el alcance es un proyecto local.

**Autonomía.** Los bucles tienen presupuesto y estados de fallo. Una petición de completar trabajo autoriza las tareas necesarias dentro de su alcance, sin preguntas ceremoniales; no autoriza push, publicación, mensajes o despliegues implícitos.

## Qué significa “original”

Los nombres de proyectos y enlaces permiten auditar las ideas seleccionadas. El código y los textos de Nexo se escribieron para esta distribución; no se copiaron módulos, SKILL.md ni instaladores upstream. Por tanto, no se atribuyen a Nexo sus pruebas, cifras de rendimiento o garantías.

Si posteriormente se integra material externo sustancial, se deberá identificar archivo, revisión, licencia y avisos correspondientes. Esta tabla no constituye permiso para redistribuir cualquier archivo de los repos.
