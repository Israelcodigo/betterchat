---
name: nexo-orchestrate
description: Coordina tareas Nexo con subagentes nativos y MCP dentro de Codex, o con el runner CLI en un entorno compatible, conservando dependencias, presupuestos y evidencia de verificación.
---

# Orquestar tareas

Utiliza esta skill para ejecutar tareas Nexo en {{PROJECT}}. El coordinador conserva el registro de tareas; los trabajadores implementan contratos acotados. Un trabajador no inicia otra orquestación.

## Elegir la vía de ejecución

**Dentro de una sesión de Codex en Windows, usa por defecto subagentes nativos del host y herramientas MCP Nexo.** Este modo usa la delegación prevista por el cliente y sus permisos. No necesita arrancar otro Codex CLI dentro del sandbox del proceso actual.

El runner CLI es la alternativa para una terminal externa o un entorno donde el cliente hijo pueda usar su perfil con los permisos nativos disponibles. En Windows, un Codex CLI hijo puede encontrar su perfil y aun así tener bloqueadas las escrituras que necesita en él. Ese fallo no demuestra un problema de la tarea y no debe convertirse en reintentos repetidos del mismo proceso.

Si el usuario pidió expresamente probar o usar el runner CLI, conserva esa elección: explica la limitación observada y distingue cualquier propuesta de delegación nativa de la prueba solicitada. No declares que el runner funcionó por haber completado la tarea mediante otra vía. Si las herramientas nativas o MCP no están disponibles, indícalo y utiliza solo una alternativa compatible con el alcance acordado.

## Modo nativo: subagentes de Codex y MCP

### Preparar el registro y el presupuesto

1. Consulta nexo_status. Si initialized es false y el trabajo requiere seguimiento Nexo, usa nexo_initialize para crear únicamente los datos del proyecto activo. Verifica que la ruta devuelta corresponde al proyecto del usuario.
2. Consulta nexo_ready_tasks y lee el contrato completo de cada candidata. Conserva tareas existentes; usa nexo_task_add solo para trabajo autorizado que falte en el registro. Comprueba las dependencias y los checks de .nexo/config.json antes de ejecutar.
3. Fija concurrencia e intentos antes de delegar. Usa un trabajador por defecto; como máximo dos, sin superar la capacidad nativa disponible ni el límite del proyecto. Toma el menor presupuesto de ciclos entre lo pedido por el usuario y la configuración del proyecto; si no hay uno explícito, usa como máximo tres ciclos. Un ciclo admite como máximo una reclamación por tarea; reparar una tarea fallida consume el siguiente ciclo. Respeta también max_attempts de cada tarea. No incrementes límites al agotarlos.
4. Determina el cwd y la propiedad de archivos de cada tarea. **Reclamar una tarea por MCP no crea un worktree.** Sin un workspace aislado ya preparado y registrado, trabaja en el cwd del proyecto y serializa las escrituras. Usa dos implementadores solo cuando sus workspaces y contratos permitan independencia real. Detén las escrituras sobre un workspace antes de verificarlo.

### Reclamar, delegar y conservar el lease

El coordinador llama nexo_task_claim con task_id, un owner que identifique esta sesión y lease_seconds suficiente para la ventana de supervisión, por ejemplo 600. Guarda el lease_token devuelto y el vencimiento. El trabajador no recibe propiedad del registro y no necesita ese token.

Usa spawn_agent con agent_type="nexo-implementer" y fork_turns="none". En su mensaje incluye:

- ID, objetivo, contrato, dependencias resueltas y criterios de aceptación.
- Cwd absoluto asignado, archivos que puede modificar y cambios ajenos que debe preservar.
- Checks y evidencia que debe devolver, con sus limitaciones.
- La instrucción de no reclamar ni completar tareas, no escribir el estado Nexo, no iniciar runners o suborquestaciones, y no integrar ni publicar por su cuenta.

Pasa el contrato en el mensaje; un fork completo del historial hereda el rol del padre y es incompatible con seleccionar este rol personalizado. Conserva el identificador real devuelto por spawn_agent para supervisar al trabajador.

Espera mediante las herramientas nativas de seguimiento en intervalos acotados. Entre esperas, renueva con nexo_task_heartbeat usando task_id, token y lease_seconds, siempre antes del vencimiento; una espera de 30 segundos con lease de 600 deja margen para supervisar. Renueva también durante revisión y pruebas si la tarea continúa running. Si el token se rechaza, no lo fuerces: inspecciona el propietario y detén las mutaciones de ese trabajador hasta reconciliar el estado.

Que un subagente termine no completa la tarea. Lee su resultado y comprueba el checkout y el diff reales. Cuando el riesgo o el contrato lo requieran, delega revisión a nexo-reviewer y pruebas a nexo-tester mediante el mismo mecanismo nativo, con cwd y contrato explícitos. Cuenta también estos roles dentro de la concurrencia acordada. Pide revisión sin escrituras de código o estado; los tests no sustituyen los gates del coordinador.

### Entregar, verificar y avanzar el grafo

Cuando la propuesta esté lista y el coordinador conserve el token, llama nexo_task_finish con status="review". Después llama nexo_verify_task con task_id y complete=true. Esta llamada ejecuta los checks configurados sobre el workspace de la tarea y vincula la evidencia al contenido actual. Solo su resultado satisfactorio y el estado done permiten tratarla como completada; el mensaje del implementador no es evidencia suficiente.

Vuelve a consultar nexo_status y nexo_ready_tasks. Lanza una dependiente solo cuando el registro la muestre desbloqueada. En un proyecto sin worktrees, las tareas sucesivas consumen los cambios verificados de ese checkout; no afirmes que hubo commits, merges o aislamiento si no ocurrieron.

En tareas de auditoría o revisión, los checks deben comprobar el entregable y
sus reproducciones, además de los checks generales pertinentes. Una auditoría
terminada puede documentar defectos abiertos del producto; no los marques como
corregidos. Antes de responder, reconcilia todas las tareas delegadas: completa
las que cumplan su contrato o explica por qué siguen en review. Ejecutar verify
sin task_id y complete=true no realiza ese cierre.

Si la implementación falla, registra nexo_task_finish con status="failed" y una causa concreta mientras el token siga vigente. Usa blocked para una dependencia externa ausente. Si nexo_verify_task falla, consulta el estado: puede haber dejado la tarea failed y liberado ya el token; no vuelvas a entregar con ese token.

Un reintento necesita diagnóstico, presupuesto restante y una nueva reclamación. El MCP actual no expone task_retry: utiliza la CLI de datos Nexo para la transición y vuelve a reclamar por MCP. Esto no inicia un cliente de modelos:

~~~powershell
& {{NEXO_COMMAND}} task show 'ID_REAL'
& {{NEXO_COMMAND}} task retry 'ID_REAL'
~~~

No reintentes tareas bloqueadas sin resolver primero la causa ni reinicies max_attempts. Al agotarse el presupuesto, conserva el fallo y la evidencia, detén la ejecución y explica lo pendiente. Una petición de estado no autoriza iniciar otra ronda.

## Modo runner CLI en un entorno compatible

Comprueba cliente instalado/autenticado, tareas listas, checks apropiados y un commit base para worktrees. Usa el backend elegido por el usuario o el configurado. Mantén los modelos, permisos y sandbox nativos. El runner copia el estado de HEAD a worktrees; no copia cambios sin commit del checkout coordinador.

~~~powershell
& {{NEXO_COMMAND}} doctor
& {{NEXO_COMMAND}} task list
& {{NEXO_COMMAND}} task ready
& {{NEXO_COMMAND}} run --backend codex --workers 1 --cycles 1
~~~

Para Claude Code, el comando alternativo es:

~~~powershell
& {{NEXO_COMMAND}} run --backend claude --workers 1 --cycles 1
~~~

No ejecutes ambas alternativas consecutivamente salvo que esa sea la intención. El runner reclama tareas, renueva leases, guarda logs y ejecuta la verificación. Un exit code cero del proceso no basta para completar: inspecciona status y verification por tarea. Un fallo de acceso del cliente al perfil se informa como límite del entorno; no se repite en un bucle externo.

## Integración y resultado

La delegación nativa no implica ejecutar el runner ni integrar ramas. Si hubo un worktree registrado y la integración local está autorizada, inspecciona primero su diff y evidencia vigente; después usa:

~~~powershell
& {{NEXO_COMMAND}} integrate 'ID_REAL'
~~~

El destino se vuelve a comprobar y verificar. Una tarea realizada directamente en el checkout del proyecto no necesita un merge ficticio. No hagas push, merge de PR o despliegue como consecuencia implícita de una tarea completada.

Devuelve la vía realmente utilizada, tareas terminadas, propuestas pendientes, tareas bloqueadas/fallidas, checks y rutas de evidencia. Distingue subagentes nativos, procesos CLI reales y ejecutores simulados; ninguno demuestra por sí solo que las otras vías funcionen.
