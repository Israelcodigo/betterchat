---
name: nexo-learn
description: Registra procedimientos reutilizables comprobados y convierte su repetición en skills verificadas, cuando la automejora está autorizada o el usuario solicita aprender de tareas repetidas.
---

# Aprender de procedimientos comprobados

Trabaja sobre {{PROJECT}}. Este flujo conserva observaciones locales y produce skills; no entrena el modelo ni captura conversaciones automáticamente. Aplícalo al terminar un procedimiento que merezca reutilizarse, sin añadir aprendizaje a cada respuesta trivial.

## Comprobar alcance y antecedentes

Lee las preferencias e inspecciona patrones del proyecto activo:

~~~powershell
& {{NEXO_COMMAND}} personal preferences
& {{NEXO_COMMAND}} learn list
~~~

Cuando auto_learn esté activado, el usuario ya autorizó crear, validar e instalar skills dentro del alcance indicado en las instrucciones personales; no pidas confirmación en cada tarea. Una petición explícita de crear una skill también autoriza su trabajo correspondiente. Si no existe ninguna de esas autorizaciones, no actives la automejora por tu cuenta. Una denegación del cliente prevalece: conserva los artefactos y reporta la limitación sin forzar escrituras ni permisos.

Busca primero una skill existente que cubra el procedimiento. Define una clave estable y concreta para el mismo tipo de tarea; no agrupes trabajos distintos solo porque usen la misma herramienta. Las observaciones pertenecen al proyecto actual, incluso cuando la futura skill sea portable. Inicializa con init --state-only únicamente si necesitas el registro y todavía no existe estado.

## Registrar una petición real

El coordinador registra el procedimiento tras resolverlo y comprobarlo; los trabajadores no registran otra observación del mismo encargo. Usa un request-id estable que identifique esta petición concreta; los reintentos, subtareas y comprobaciones adicionales de la misma petición conservan su ID. No inventes repeticiones ni cuentes ejemplos generados por el agente como solicitudes distintas del usuario.

~~~powershell
& {{NEXO_COMMAND}} learn record 'clave-del-procedimiento' --request-id 'ID_REAL_DE_PETICION' --summary 'Procedimiento, alcance y resultado observado' --evidence 'ruta/artefacto-real' --check-json '["programa","argumento-del-check"]'
~~~

Sustituye los ejemplos por IDs, archivos y checks reales. --evidence es repetible; --check-json contiene un array de argumentos, sin interpolación de shell, y --timeout limita su duración (120 segundos por defecto). El runtime ejecuta el check y registra hashes de los archivos de evidencia. Selecciona un check síncrono que pruebe la aceptación del resultado, sin abrir daemons ni dejar procesos en segundo plano. Usa un script pequeño si la verificación necesita varias operaciones.

El registro es idempotente por petición y un fallo reinicia la racha. No cambies SQLite/JSON ni los IDs para convertir un fallo o un único encargo en tres éxitos. Evita secretos, historiales completos y datos personales; conserva solo evidencia pertinente del proyecto.

### Corregir un registro fallido

Los archivos de --evidence deben existir antes del check y permanecer **inmutables** mientras se ejecuta. No declares como evidencia el informe que ese mismo check vuelve a escribir. Usa entradas/código estables y un check con aserciones sobre el resultado; genera informes auxiliares en otra ruta. Si el informe ya se generó, un check de solo lectura puede validarlo. El recibo identifica los archivos que cambiaron y conserva sus hashes anteriores y posteriores.

Repetir learn record devuelve el recibo anterior sin ejecutar nada. Cuando hayas corregido un fallo de la misma petición, usa un reintento explícito con el **mismo request-id**, una razón concreta y el check, resumen y evidencia corregidos:

~~~powershell
& {{NEXO_COMMAND}} learn history 'clave-del-procedimiento' --request-id 'ID_REAL_DE_PETICION'
& {{NEXO_COMMAND}} learn retry 'clave-del-procedimiento' --request-id 'ID_REAL_DE_PETICION' --reason 'Causa comprobada y corrección aplicada' --summary 'Procedimiento y alcance comprobados' --evidence 'ruta/entrada-estable' --check-json '["programa","argumento-del-check-corregido"]'
~~~

retry conserva el original y cada ejecución posterior en history, sin añadir otra observación ni renovar la fecha de la petición. Rechaza reintentos mientras el check está ejecutándose y después de un éxito. Un éxito corregido cuenta una sola vez. Si existía una candidata para este patrón, el reintento invalida explícitamente su generación y sus recibos dependientes: vuelve a ejecutar stage y una validación sobre un caso nuevo antes de instalar. No reemplaza automáticamente una skill ya instalada. Un fallo de learn validate exige un caso nuevo; retry solo corrige observaciones de peticiones reales.

## Construir una candidata después de tres éxitos

El runtime cuenta éxitos de los últimos 90 días. La validación también caduca
a los 90 días: prueba de nuevo la candidata si vas a instalarla después.

Consulta learn list. Tras tres peticiones distintas con éxito consecutivo, usa skill-creator, si está disponible, para redactar o mejorar una skill con nombre y descripción precisos. Extrae el procedimiento confirmado y sus condiciones; no conviertas preferencias inferidas o incidentes aislados en reglas universales. Antes de actualizar una skill existente, revisa su propiedad y alcance: no sobrescribas instrucciones ajenas.

Prepara la candidata en .nexo/skill-drafts/nombre-de-la-skill, separada de .nexo/learned-skills, que es el destino administrado de stage. Incluye solo scripts, referencias o recursos que aporten una capacidad comprobable. La descripción debe permitir selección implícita; mantén la invocación automática habilitada salvo que el usuario haya pedido explícitamente otra cosa. Si skill-creator no está disponible, conserva la candidata y documenta esa limitación en vez de afirmar que seguiste su validación.

~~~powershell
& {{NEXO_COMMAND}} learn stage 'clave-del-procedimiento' --skill-dir 'ruta/candidata'
~~~

## Probar en un caso nuevo e instalar

Ejecuta la candidata sobre un caso representativo distinto de las tres peticiones registradas, en un espacio aislado y dentro de las acciones autorizadas. Una prueba sintética nueva sirve para evaluar la skill; no cuenta como otra petición del usuario. Verifica el resultado esperado y revisa que el procedimiento, los scripts y la descripción no amplíen el alcance. Para skills complejas, utiliza una revisión independiente con los artefactos mínimos.

Ejecuta además quick_validate.py de skill-creator sobre la carpeta. Ese validador verifica la estructura, no la eficacia del procedimiento. Corrige cualquier fallo antes de validar el aprendizaje.

~~~powershell
& {{NEXO_COMMAND}} learn validate 'clave-del-procedimiento' --case-id 'ID_NUEVO_DEL_CASO' --evidence 'ruta/resultado-nuevo' --check-json '["programa","argumento-del-check-nuevo"]'
~~~

La validación queda vinculada al contenido de todos los archivos de la skill y al caso nuevo. Si editas la candidata después, repite su prueba y validación; no reutilices un recibo obsoleto.

Elige el alcance por contenido: un procedimiento específico de este código, sus rutas o sus reglas se instala en .agents/skills del proyecto. Uno general, portable y sin rutas particulares, datos privados o secretos puede instalarse en el perfil personal autorizado, disponible en todos los proyectos.

~~~powershell
& {{NEXO_COMMAND}} learn install 'clave-del-procedimiento' --scope project
~~~

Para una candidata portable autorizada usa --scope personal. Solo añade --codex-home si el perfil de destino ya es conocido y pertinente; no selecciones otro perfil para sortear una denegación. El runtime conserva respaldos de sus propias instalaciones y rechaza colisiones con contenido ajeno. Si hay conflicto, informa y conserva la candidata validada.

Informa brevemente cuando se cree, actualice o instale una skill: nombre, alcance, evidencia y limitaciones. Distingue instalación en disco de carga por el cliente; una sesión nueva puede ser necesaria para que aparezca en su catálogo. No afirmes que una skill fue aplicada en una sesión real si solo pasó pruebas locales.
