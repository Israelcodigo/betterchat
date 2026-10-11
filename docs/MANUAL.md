# Nexo Kit

Kit local de skills y orquestación para **desarrollo general en Windows con Claude Code y Codex CLI**. Une planificación, tareas con dependencias, ejecución acotada, worktrees, evidencia de pruebas, memoria de proyecto y recuperación de contexto.

El código y las instrucciones son originales. Nexo usa tus clientes de Claude Code o Codex; no necesita instalar los harnesses investigados, otro proveedor de modelos, tmux, Docker ni WSL.

## Qué incluye

**Nuevo en 1.2:** [panel local de skills, agentes, actividad y aprendizaje](DASHBOARD.md).
Abre el acceso directo **Nexo Panel** o ejecuta `nexo dashboard` desde un proyecto.
Desde **1.2.2**, `nexo personal install --auto-open-dashboard` activa su apertura
al usar Nexo, con detección de un panel conectado para evitar duplicados.

| Pieza | Comportamiento |
|---|---|
| 9 skills | Planificar, construir, revisar, verificar, diagnosticar, recordar, reanudar, orquestar y aprender procedimientos repetidos. |
| 4 roles | Planner, implementer, reviewer y tester; sin fijar modelos o esfuerzo. |
| Panel local | Descripciones e instrucciones completas, actividad observada/declarada y aprendizaje separado por proyecto. |
| Estado local | SQLite con tareas, dependencias, intentos, leases, eventos y memorias. |
| Runner | Procesos de Codex, Claude o un ejecutor explícito; límites de workers, ciclos y timeout. |
| Git | Worktrees por tarea, herencia de dependencias terminadas e integración local explícita. |
| Evidencia | Comandos de prueba, logs, códigos de salida, JUnit opcional y fingerprint del contenido/configuración. |
| Contexto | Captura de salida, búsqueda, lectura por ID y checkpoints acotados. |
| Aprendizaje | Memorias candidatas, aprobación, archivo y generación de candidatas de skills. |
| Integraciones | Skills, roles, instrucciones, hooks y servidor MCP stdio para cada host. |
| Instalador | Instalación personal para todos los proyectos de Codex o instalación por proyecto para Codex/Claude; combina configuración y conserva modificaciones del usuario. |

La revisión del modelo complementa los checks; no es una garantía automática. **Done significa que los checks configurados pasaron sobre el contenido registrado**, no que se haya demostrado cualquier propiedad del programa.

## Requisitos

- Windows con Python **3.11 o posterior**. Los ejemplos usan uv para ejecutar Python 3.13; también puedes usar tu intérprete ya instalado.
- Git disponible para worktrees e integración. El proyecto necesita al menos un commit para iniciar trabajo aislado.
- Para tareas con modelos: Codex CLI o Claude Code instalado, accesible y autenticado, con sus permisos normales.
- Para la demostración sin modelos ni credenciales: Python y Git; crea su propio repositorio temporal.

El runtime usa la biblioteca estándar de Python y no tiene dependencias de paquetes. uv puede descargar Python la primera vez; con el intérprete ya disponible, las pruebas locales no necesitan acceso a un modelo.

Nexo admite dos alcances: **personal para Codex**, disponible en todos tus proyectos, y **por proyecto para Codex o Claude Code**. Ambos guardan una copia propia del runtime, por lo que después puedes mover o retirar este checkout fuente. La ruta del intérprete utilizada durante la instalación debe seguir existiendo.

## Instalación personal para todos tus proyectos de Codex

Desde PowerShell, en la carpeta del kit:

~~~powershell
uv run --no-project --python 3.13 python nexo.py personal install --dry-run
uv run --no-project --python 3.13 python nexo.py personal install
~~~

El destino predeterminado es CODEX_HOME, si está definido, o la carpeta .codex de tu usuario. Para elegir otro perfil, añade --codex-home 'C:\ruta\perfil-codex' a ambos comandos. Se instalan las nueve skills, cuatro roles, hooks, instrucciones y MCP en ese perfil. Este modo personal solo configura Codex; para Claude usa la instalación por proyecto descrita abajo.

La selección por intención es automática. Para autorizar además la creación e instalación de skills tras tres peticiones satisfactorias y una validación nueva, añade `--auto-learn` a `personal install`. Esta preferencia se conserva al actualizar. [Funcionamiento y límites del aprendizaje](AUTOMATIC-LEARNING.md).

**Abre una sesión nueva de Codex** para cargar skills, roles y configuración MCP. Revisa los hooks y su confianza con los controles nativos del cliente. El instalador no concede confianza automática, no cambia el modelo ni desactiva permisos del host. Una actualización que cambie comandos de hooks puede necesitar una nueva revisión de confianza.

La disponibilidad es global; **tareas, memoria, evidencia y checks siguen separados por proyecto**. Los comandos y MCP personales usan el directorio activo de la sesión. Los hooks toman el cwd del payload del cliente, que puede diferir del cwd de su proceso. No hay una ruta de proyecto fija en la configuración global.

Abrir un proyecto, consultar nexo_status o ejecutar hooks no crea estado automáticamente. Cuando quieras usar el registro Nexo, pide al agente que invoque la herramienta MCP nexo_initialize, o inicializa únicamente los datos de ese proyecto:

~~~powershell
uv run --no-project --python 3.13 python nexo.py init 'C:\dev\Mi proyecto' --state-only
~~~

Este comando crea .nexo/config.json, SQLite y checkpoints, y añade .nexo/ a .gitignore. No duplica skills, roles, hooks ni MCP dentro del repositorio. Inicializar otra vez conserva los datos y checks existentes. Puedes inspeccionar el destino antes de escribir añadiendo --dry-run.

La instalación personal conserva entradas ajenas en config.toml, hooks.json y AGENTS.md. Sus aportaciones quedan identificadas y el manifiesto conserva originales para restauración. Si modificas una skill, un rol o el contenido de un bloque administrado por Nexo, la reinstalación señala el conflicto antes de sobrescribirlo.

## Instalación por proyecto en PowerShell

Abre PowerShell en la carpeta de este kit. Sustituye la ruta de ejemplo por tu repositorio:

~~~powershell
$nexoKit = (Get-Location).Path
$nexoProject = 'C:\dev\Mi proyecto'

uv run --no-project --python 3.13 python nexo.py init $nexoProject
uv run --no-project --python 3.13 python nexo.py --project $nexoProject doctor
~~~

Para inspeccionar las rutas que se escribirán antes de instalar, añade --dry-run a init. Para un único host, añade --host codex o --host claude. También existen --no-hooks y --no-mcp si quieres solo skills, roles e instrucciones.

Si ya elegiste la instalación personal para Codex, usa init --state-only en lugar de instalar otra copia de sus integraciones en cada repositorio.

En cualquiera de los dos alcances puedes definir un atajo únicamente para esta ventana. Desde la carpeta del kit:

~~~powershell
$nexoKit = (Get-Location).Path
$nexoProject = 'C:\dev\Mi proyecto'

function Invoke-Nexo {
    uv run --no-project --python 3.13 python (Join-Path $nexoKit 'nexo.py') --project $nexoProject @args
}

Invoke-Nexo status
~~~

Este atajo no se escribe en tu perfil. Los comandos siguientes lo usan. Si ya tienes Python disponible, sustituye la parte “uv run --no-project --python 3.13 python” por tu intérprete, conservando el resto de argumentos.

La instalación no concede confianza automáticamente a hooks o MCP. Revisa y habilita la configuración mediante los controles nativos de tu cliente y abre una sesión nueva cuando corresponda.

## Configurar las comprobaciones del proyecto

Elige comandos que ya prueben el comportamiento de tu proyecto. Por ejemplo, para un repositorio con tests unittest:

~~~powershell
Invoke-Nexo check add 'unittest' --command-json '["{python}","-B","-m","unittest","discover","-s","tests"]'
Invoke-Nexo check list
Invoke-Nexo verify
~~~

**El ejemplo supone que existen esos tests.** Para Node, .NET, Go u otro entorno, registra su ejecutable y sus argumentos reales. Un check es un array JSON de argumentos, no una línea de shell. Se ejecuta en el workspace que se está verificando.

Se admiten estos marcadores dentro de los argumentos:

| Marcador | Sustitución |
|---|---|
| {python} | Intérprete que ejecuta Nexo. |
| {project} | Directorio del proyecto coordinador. |
| {worktree} | Directorio concreto de la tarea; el proyecto si no hay worktree. |

Usa --timeout para ajustar el límite del check y --junit para un informe JUnit que el comando realmente regenere:

~~~powershell
Invoke-Nexo check add 'pytest' --command-json '["{python}","-B","-m","pytest","--junitxml=.nexo/test-results.xml"]' --timeout 300 --junit '.nexo/test-results.xml'
~~~

Este segundo ejemplo requiere pytest y los tests correspondientes en el entorno del proyecto; Nexo no los instala. No necesitas registrar ambos ejemplos.

Los ejemplos de JSON están pensados para el paso de argumentos de PowerShell moderno. Si tu terminal altera las comillas al invocar ejecutables, puedes registrar el mismo check directamente en .nexo/config.json:

~~~json
{
  "version": 1,
  "checks": [
    {
      "name": "unittest",
      "command": ["{python}", "-B", "-m", "unittest", "discover", "-s", "tests"],
      "timeout_seconds": 120
    }
  ]
}
~~~

Conserva las demás claves existentes al editar. Los nombres de checks deben ser únicos. Los campos omitidos de runner/context/hooks reciben valores predeterminados.

Checks vacíos, fallos, timeouts, informes JUnit ausentes o antiguos y cambios de contenido durante la verificación impiden completar la tarea. Si un check modifica código fuente, aplica primero sus cambios y verifica otra vez sobre un estado estable. No añadas un check que siempre pase para desbloquear el runner.

## Crear tareas y ejecutar

Una tarea necesita un resultado y una forma de comprobarlo:

~~~powershell
$first = Invoke-Nexo task add 'Corregir rutas con espacios' --description 'La CLI debe aceptar una ruta Windows con espacios sin escribir fuera del proyecto. Añadir una regresión por la interfaz pública y ejecutar la suite existente.' --max-attempts 3 | ConvertFrom-Json

$second = Invoke-Nexo task add 'Documentar las rutas admitidas' --description 'Actualizar los ejemplos con la sintaxis comprobada por la tarea anterior; comprobar que las rutas y los comandos del documento existen.' --depends $first.id | ConvertFrom-Json

Invoke-Nexo task ready
~~~

Usa criterios propios de tu cambio. El ejemplo muestra cómo reutilizar los IDs devueltos sin inventarlos; no es una lista de tareas obligatoria.

Ejecutar con Codex:

~~~powershell
Invoke-Nexo run --backend codex --workers 1 --cycles 1
~~~

O ejecutar con Claude Code:

~~~powershell
Invoke-Nexo run --backend claude --workers 1 --cycles 1
~~~

Son alternativas. El runner crea trabajo aislado a partir de HEAD; **no copia cambios sin commit del checkout coordinador**. Revisa el estado Git antes de comenzar y decide qué modificaciones deben formar parte de la base. No se realizan commits, stashes o descartes de tu checkout principal automáticamente para prepararla.

Un trabajador termina su propuesta, el runner conserva el resultado en su rama y ejecuta los checks. Si pasan, la tarea puede quedar done directamente. Si falla la verificación, conserva la evidencia y permite reparación dentro del presupuesto. Las tareas dependientes reciben los commits de sus prerrequisitos completados.

Por defecto, la configuración permite 2 workers, 3 ciclos, 900 segundos por proceso y leases de 120 segundos con heartbeat. Los ejemplos eligen 1 worker y 1 ciclo para una primera ejecución fácil de inspeccionar. Paralelismo requiere worktrees; --no-worktrees solo admite un trabajador y modifica directamente el proyecto.

Los límites de ciclos y --max-attempts se aplican conjuntamente. Un ciclo puede recorrer varias tareas del grafo; no equivale a una única llamada al modelo. No hay un daemon que continúe trabajando después de terminar el proceso runner.

## Revisar e integrar

~~~powershell
Invoke-Nexo task show $first.id
Invoke-Nexo status
~~~

Lee el diff de la rama, la evidencia y los criterios no cubiertos. Usa nexo-review para una revisión independiente si resulta útil. La revisión es consultiva; Nexo no afirma que haya ocurrido solo porque pasaron tests.

Para una propuesta manual en review:

~~~powershell
Invoke-Nexo verify --task $first.id --complete
~~~

No uses --complete sobre una tarea que ya está done: ese flag finaliza review. Un fallo de esta comprobación deja la tarea failed y permite un reintento explícito. Ejecutar verify sin --complete solo produce evidencia; no completa la tarea.

Integración local de una tarea done con worktree:

~~~powershell
Invoke-Nexo integrate $first.id
~~~

La integración exige destino limpio y evidencia vigente, fusiona el commit y vuelve a ejecutar checks en destino. Si la verificación integrada falla y el destino permanece limpio, crea una reversión visible del merge. Si los checks dejaron modificaciones, conserva el trabajo y pide inspeccionarlo.

No hace push, publica PR ni despliega. No borra worktrees al terminar: conserva ramas y evidencia para revisión. Resuelve conflictos con las herramientas Git normales y el contexto del proyecto, sin recurrir a un reset destructivo.

Una tarea done modificada después de verificarse puede tener evidencia obsoleta. Esta versión no la reabre automáticamente: registra una tarea sucesora para la corrección y conserva el historial. Un verify sin --complete no reemplaza el recibo almacenado de una tarea ya completada.

## Trabajo manual y recuperación

También puedes usar tareas y evidencia mientras implementas en tu sesión habitual:

~~~powershell
$claim = Invoke-Nexo task claim $first.id --owner 'sesion-manual' --lease-seconds 3600 | ConvertFrom-Json

# Implementa el contrato y renueva antes de vencer si hace falta.
Invoke-Nexo task heartbeat $first.id --token $claim.lease_token --lease-seconds 3600

# Entrega una propuesta; todavía no equivale a done.
Invoke-Nexo task finish $first.id --token $claim.lease_token --status review
Invoke-Nexo verify --task $first.id --complete
~~~

Este bloque es una alternativa al runner: sustituye $first.id por el ID de una tarea pending y desbloqueada si la del ejemplo anterior ya terminó. Consulta primero su estado. El trabajo manual se hace en el workspace apropiado; la reclamación no crea por sí sola un nuevo worktree.

Cuando falle una ejecución, conserva la causa:

~~~powershell
Invoke-Nexo task finish $first.id --token $claim.lease_token --status failed --error 'Describe el fallo observado y el siguiente experimento.'
~~~

Es una alternativa a terminar en review, no un comando para ejecutar después con el mismo token liberado. Para un insumo externo ausente, usa blocked.

Recuperar reclamaciones vencidas y reintentar trabajo después de resolver su causa:

~~~powershell
Invoke-Nexo task recover
Invoke-Nexo task retry $first.id
~~~

Una recuperación invalida tokens antiguos. No se fuerza propiedad ni se reinician presupuestos agotados silenciosamente. Un Ctrl+C conserva estado; inspecciona los leases antes de retomar.

## Skills y roles

| Skill | Uso |
|---|---|
| nexo-plan | Contratos con criterios, archivos propios y dependencias reales. |
| nexo-build | Implementación acotada y propuesta con diagnóstico/evidencia. |
| nexo-review | Hallazgos accionables, separados del relato del implementador. |
| nexo-verify | Comprobaciones reales y evidencia vigente. |
| nexo-debug | Reproducción, hipótesis y reparación limitada. |
| nexo-memory | Recuperar, proponer, aprobar y promover conocimiento. |
| nexo-resume | Reconciliar el estado después de reiniciar o compactar. |
| nexo-orchestrate | Ejecutar el DAG y supervisar sus resultados. |

Las instrucciones personales permiten seleccionar skills según la tarea sin invocarlas por nombre. También puedes escribir $nexo-plan en Codex o /nexo-plan en Claude Code. Los clientes descubren las skills mediante sus rutas personales o locales. Los roles base son nexo-planner, nexo-implementer, nexo-reviewer y nexo-tester; el panel incorpora las extensiones Nexo instaladas. Si el cliente expone agent_type en spawn_agent, selecciona el rol con fork_turns="none" y transmite su contrato. Si esa opción no está disponible, delega el contrato a un subagente genérico y distingue esa ejecución de la carga de un rol personalizado.

Los agentes disponen de criterios y comandos; no tienen que activar todas las skills para cada cambio. Una edición pequeña puede resolverse directamente.

## Memoria y candidatas de skills

Registrar una observación no la activa como regla:

~~~powershell
$lesson = Invoke-Nexo memory add 'En este proyecto las rutas se transmiten como argumentos separados. Evidencia: prueba de integración con espacios. Revisar si cambia el mecanismo de lanzamiento.' --kind correction --source 'tests/test_paths.py' | ConvertFrom-Json

Invoke-Nexo memory search 'rutas espacios' --include-candidates
Invoke-Nexo memory list
~~~

Usa una fuente real de tu proyecto. Comprueba el registro y su evidencia antes de aprobarlo:

~~~powershell
Invoke-Nexo memory approve $lesson.id
Invoke-Nexo memory search 'rutas espacios'
Invoke-Nexo memory promote $lesson.id --name 'rutas-del-proyecto'
~~~

La búsqueda normal devuelve solo registros activos. La promoción requiere una memoria activa con fuente y genera un **SKILL.md candidato en .nexo/learned-skills**. No lo instala ni prueba automáticamente: revisa el texto y valida su procedimiento antes de activarlo en un host.

Ese comando de memoria conserva su comportamiento manual. El flujo nuevo `learn` registra peticiones distintas, ejecuta checks, valida una skill completa sobre un caso nuevo y permite su instalación autorizada. Lo coordina `nexo-learn` cuando la preferencia personal está activada; no promueve memorias arbitrarias. [Guía de automejora](AUTOMATIC-LEARNING.md).

También puedes relacionar memorias con memory link y retirar una regla con memory archive. La confianza es un dato heurístico, no una probabilidad de que sea correcta. Nexo no reentrena modelos ni convierte cada mensaje en una regla global.

## Contexto, checkpoints y exportación

Captura explícitamente un archivo que quieras recuperar sin incluirlo completo en el contexto:

~~~powershell
$capture = Invoke-Nexo context capture --file 'C:\dev\Mi proyecto\logs\build.log' --source 'build-local' | ConvertFrom-Json
Invoke-Nexo context search 'error'
~~~

Lee el ID devuelto por la búsqueda con context fetch:

~~~powershell
Invoke-Nexo context fetch 'ID_DEVUELTO' --offset 0 --limit 2000
~~~

Hay límites de captura y de respuesta. La búsqueda local usa FTS5/BM25 cuando está disponible y una alternativa lexical cuando no. Las salidas de workers también pueden quedar indexadas. Esto gestiona el contexto; no crea un sandbox del sistema operativo.

~~~powershell
Invoke-Nexo checkpoint
Invoke-Nexo export | Set-Content -LiteralPath (Join-Path $nexoProject '.nexo\snapshot.json') -Encoding utf8
~~~

Checkpoint regenera plan.md, findings.md, progress.md y checkpoint.md dentro de .nexo. Son vistas derivadas; el estado canónico está en SQLite. Export produce un snapshot JSON consistente del registro de tareas, memorias y eventos. No incluye todos los logs/worktrees y no hay una operación automática de importación: no lo confundas con una copia completa restaurable del proyecto.

## Hooks y MCP

Los adaptadores generan eventos SessionStart, PreCompact, PostToolUse y Stop según el contrato del host. SessionStart aporta contexto acotado; los otros eventos conservan información y checkpoints. No hay un bucle Stop que obligue indefinidamente a continuar.

En la instalación personal, los cuatro hooks permanecen sin escrituras si el proyecto aún no tiene .nexo/config.json. SessionStart informa de que Nexo está disponible y de cómo inicializarlo cuando sea útil. El estado se crea solo mediante una inicialización explícita.

La captura de salida de herramientas está desactivada inicialmente. Se habilita por proyecto con hooks.capture_tool_output en .nexo/config.json; evita volver a capturar las propias consultas Nexo. Los workers del runner omiten hooks de checkpoint para evitar escritores duplicados.

El MCP stdio expone **13 herramientas**:

| Herramientas | Función |
|---|---|
| nexo_status, nexo_initialize | Consultar estado e inicializar datos del proyecto activo bajo demanda. |
| nexo_ready_tasks, nexo_task_add | Encontrar trabajo desbloqueado y registrar tareas. |
| nexo_task_claim, nexo_task_heartbeat, nexo_task_finish | Reclamar trabajo, renovar su lease y entregar una propuesta o registrar un fallo. |
| nexo_memory_search, nexo_memory_propose | Buscar memorias activas y proponer candidatas. |
| nexo_context_search, nexo_context_fetch | Buscar salidas persistidas y leer fragmentos. |
| nexo_checkpoint, nexo_verify_task | Recuperar contexto y ejecutar comprobaciones configuradas. |

El servidor puede arrancar, anunciar sus herramientas y responder a nexo_status antes de inicializar el proyecto. Las operaciones que necesitan datos requieren nexo_initialize o init --state-only primero. No ofrece una herramienta genérica para ejecutar cualquier comando. nexo_verify_task sí ejecuta los checks que hayas configurado para el proyecto.

El control de confianza y permisos sigue en el host. Nexo no intercepta ni valida cada comando de todas las herramientas.

## Archivos que se instalan

Con la instalación personal de Codex:

~~~text
CODEX_HOME/                 # Por defecto, .codex de tu usuario
  nexo-kit/
    runtime/nexo.py
    runtime/nexo_kit/
    install-manifest.json
  skills/nexo-*/
  agents/nexo-*.toml
  hooks.json
  config.toml
  AGENTS.md
~~~

Cada proyecto activado con nexo_initialize o init --state-only conserva sus datos bajo su propia .nexo, sin copiar otra vez estas integraciones.

Con la instalación completa por proyecto:

~~~text
proyecto/
  .nexo/
    config.json
    state.sqlite3
    runtime/nexo.py
    runtime/nexo_kit/
    install-manifest.json
    worktrees/               # Si se usa ejecución aislada
    workspaces/              # Rutas y commits de cada tarea
    runs/                    # Prompts, logs y resultados
    evidence/                # Recibos y logs de checks
    learned-skills/          # Candidatas, sin instalar
    plan.md
    findings.md
    progress.md
    checkpoint.md
  .agents/skills/nexo-*/     # Codex
  .codex/agents/nexo-*.toml
  .codex/hooks.json
  .codex/config.toml
  .claude/skills/nexo-*/
  .claude/agents/nexo-*.md
  .claude/settings.json
  .mcp.json
  AGENTS.md
  CLAUDE.md
~~~

Se generan los archivos del host seleccionado. .nexo se añade a .gitignore. Los ajustes se combinan por las claves correspondientes y los bloques de instrucciones tienen marcadores; no se reemplaza todo un AGENTS.md o CLAUDE.md.

Doctor informa de integridad SQLite, FTS5, ejecutables encontrados, checks y archivos de instalación modificados. Su indicador ready_for_autonomy no prueba autenticación de clientes ni calidad de los checks.

## Actualizar o desinstalar

Para actualizar la instalación personal, ejecuta personal install desde la versión fuente que quieras instalar. Para retirarla:

~~~powershell
uv run --no-project --python 3.13 python nexo.py personal uninstall --dry-run
uv run --no-project --python 3.13 python nexo.py personal uninstall
~~~

Si instalaste en otro perfil, conserva el mismo --codex-home. La desinstalación personal no recorre ni borra los datos de tus proyectos. Los cambios del usuario fuera de los bloques o hooks Nexo se conservan también si hubo una reinstalación intermedia. Si encuentra archivos personalizados que no puede restaurar sin perder esos cambios, devuelve complete: false y los conserva junto con su runtime y manifiesto para revisión. Abre una nueva sesión de Codex después de cambiar o retirar sus integraciones.

Para actualizar una instalación por proyecto, vuelve a ejecutar init desde la versión fuente que quieras instalar. Para retirarla:

~~~powershell
Invoke-Nexo uninstall --dry-run
Invoke-Nexo uninstall
~~~

La desinstalación retira/restaura archivos no modificados registrados en su manifiesto. Conserva los archivos que cambiaste y los datos .nexo: tareas, memoria y evidencia no se borran. Examina preserved_modified para conocer qué requiere revisión manual.

Si quedan archivos modificados que podrían seguir llamando al runtime, conserva también ese runtime y lo informa en runtime_retained. La instalación valida el TOML antes de escribir y revierte archivos ante errores de escritura; no es una transacción frente a un corte de energía.

## Demostración y pruebas

Desde la carpeta del kit:

~~~powershell
uv run --no-project --python 3.13 python -B examples/demo.py
uv run --no-project --python 3.13 python -B -m unittest discover -s tests -v
~~~

La demostración usa un ejecutor local controlado; no llama a Claude, Codex ni una API de modelos. Crea un directorio temporal que conserva al terminar y devuelve su ruta para revisar la evidencia. La primera descarga del intérprete por uv puede necesitar red.

La suite cubre estados/dependencias, reclamaciones concurrentes, tokens obsoletos, persistencia, memoria/contexto, instalación, protocolo MCP, checks y runner mediante procesos reales de prueba. La existencia de esos tests y una ejecución con workers controlados no equivale a una prueba de calidad del modelo.

Consulta los comandos de desarrollo en [VERIFICATION.md](VERIFICATION.md). Conserva los resultados de tus ejecuciones en el estado local del proyecto.

## Investigación, procedencia y límites

- [Diseño previo a la construcción](DESIGN.md).
- [Inventario de repos y niveles de evidencia](INVENTORY.md).
- [Tabla de procedencia y elecciones](PROVENANCE.md).
- [Descartes y aspectos sin resolver](DISCARDED.md).
- [Fuentes y avisos](SOURCES-NOTICE.md).
- [Inspección adicional con SHA y archivos](research/research-extra.json).
- [Inspección de memoria/contexto con SHA y archivos](research/research-memory.json).

No se verificaron benchmarks comparativos, cifras de ahorro de tokens ni afirmaciones de autonomía prolongada de los repos. Las divergencias entre investigaciones se conservan en el inventario. El kit opera en un proyecto local: no ofrece consenso distribuido, sincronización multi-máquina ni memoria semántica con embeddings.

Los logs locales pueden contener datos producidos por tus herramientas. La redacción de patrones en capturas o extractos no garantiza retirar todos los secretos; evita incluir credenciales en comandos y resultados.

## Licencia

Nexo Kit se distribuye bajo [MIT](../LICENSE). Los proyectos investigados mantienen sus propias licencias y no están incluidos como dependencias o código vendorizado.
