# Panel local de Nexo

Nexo 1.2 incorpora un panel para consultar las skills, los roles y el aprendizaje
de los proyectos locales. Se sirve en `127.0.0.1`, sin dependencias externas,
fuentes remotas ni publicación en Internet.

## Abrir y volver a abrir

En este equipo, el acceso directo **Nexo Panel** del escritorio abre el panel y
reutiliza su servidor si ya está funcionando. Después de reiniciar Windows,
vuelve a abrir ese acceso directo; no requiere iniciar manualmente una terminal.

Desde la carpeta de cualquier proyecto también puedes ejecutar:

```powershell
python nexo.py dashboard
```

En una instalación de paquete se usa `nexo dashboard`. Las operaciones
`dashboard status` y `dashboard stop` consultan o detienen el servidor propio.
`dashboard serve` lo mantiene en primer plano para diagnóstico.

El API requiere un token local que entrega el lanzador en el fragmento de la URL
y que el navegador conserva durante esa sesión. La URL sin token no concede
acceso a los datos en una ventana nueva. Usa el acceso directo para reabrirlo.
La interfaz elimina el fragmento de la barra al cargar.

## Apertura automática al usar Nexo (1.2.2)

Actívala una vez para el perfil personal con
`nexo personal install --auto-open-dashboard`. Se conserva al actualizar,
igual que la preferencia de aprendizaje. Se desactiva con
`nexo personal install --no-auto-open-dashboard`. El valor por defecto para
instalaciones nuevas es desactivado; este perfil la tiene autorizada por el usuario.

El registro `activity record --status started` de una capacidad `nexo-*`
instalada solicita abrir el panel. También lo hacen los hooks PostToolUse que
observan una lectura/ejecución satisfactoria de una skill Nexo, una delegación
confirmada a un rol Nexo instalado o una llamada MCP Nexo correcta. Una lectura
observada no prueba que el agente ya haya aplicado todas las instrucciones.
SessionStart, PreCompact, Stop, referencias y fallos no provocan aperturas.
Los workers del runner (`NEXO_WORKER=1`) no abren el navegador.

En Windows se instala `~/.codex/nexo-kit/Open-Nexo-Panel.cmd`. Sin parámetros
ejecuta `dashboard auto`: respeta la preferencia y reutiliza el panel.
`nexo dashboard` sigue permitiendo una apertura manual. Funciona desde cualquier carpeta sin
inicializar `.nexo` ni añadir esa carpeta al catálogo. El script llama directamente
al intérprete instalado de Python, sin PowerShell ni cambios de políticas de ejecución.

El navegador predeterminado de Windows recibe la apertura. El script no controla
la interfaz interna de Codex. Un panel que ya esté conectado dentro de Codex
también cuenta para evitar duplicados. El frontend envía una señal local
autenticada cada 30 segundos, aunque se pausen los datos; las consultas de estado
también renuevan la señal. El servidor la considera vigente durante dos minutos.
Al cerrar o suspender la pestaña, el siguiente uso después de ese plazo permite
reabrirla. Una pestaña suspendida más tiempo puede producir una apertura adicional.
La apertura manual siempre está disponible.

Una solicitud de apertura reserva 30 segundos para que el navegador cargue;
las llamadas simultáneas no abren varias pestañas. Si falla el navegador, no se
reintenta hasta otra señal de uso pasado ese intervalo. El resultado
`browser_opened` confirma que el SO aceptó la solicitud, no el renderizado del
panel. Los fallos no bloquean la actividad ni elevan permisos. El reporte CLI
incluye el resultado sin el token del panel; el hook conserva su respuesta normal.

## Qué muestra

- **Resumen:** inventario, capacidades con uso registrado, aprendizaje y carpetas.
- **Skills:** las nueve del kit, extensiones Nexo instaladas, candidatas y skills
  aprendidas documentadas en los registros de proyectos conectados. Incluye descripción, origen, alcance, ruta,
  instrucciones completas y botones para copiar nombre o instrucciones.
- **Agentes:** todos los roles Nexo instalados en el perfil y los proyectos, con
  su alcance, origen e instrucciones completas. Los cuatro roles base se distinguen
  de las extensiones; las instalaciones con el mismo nombre conservan su identidad. Una
  definición de rol no demuestra que haya un trabajador ejecutándose.
- **Aprendizaje:** repeticiones y rachas por procedimiento, umbral de tres éxitos,
  candidata, validación y estado de instalación de cada proyecto.
- **Actividad:** lecturas, ejecuciones, referencias, reportes del agente y eventos
  de hooks, con su origen y fecha. Se puede filtrar por skills o agentes.

La búsqueda y el selector de proyecto afectan a la vista. Las skills personales
siguen disponibles al seleccionar cualquier proyecto. Se actualiza cada cinco
segundos; permite pausar y actualizar manualmente.

## Proyectos

Los hooks registran las carpetas que usan Nexo a partir de esta versión. El botón
**Añadir proyecto** incorpora una ruta absoluta existente, aunque no tenga
`.nexo`. Consultar o añadir una carpeta no inicializa su estado.

El catálogo incorpora las carpetas registradas por el usuario y por los hooks.
Los proyectos cloud sin carpeta local no tienen un registro Nexo que este servicio
pueda leer. Un ensayo de cliente real también puede registrar su carpeta temporal;
sus comprobaciones sintéticas no cuentan como peticiones reales de aprendizaje.
La vista conjunta no suma repeticiones entre proyectos para promover una skill.

## Interpretar actividad y aprendizaje

**En uso · declarado** indica un inicio reportado por el agente sin cierre
posterior, con actividad reciente de su sesión. Los reportes sin identificador
de sesión solo conservan vigencia durante dos minutos. Un Stop, el cierre de la
fase o una sesión sin actividad reciente elimina esa indicación.

Desde 1.2.1, `activity record` toma `CODEX_THREAD_ID` cuando no se proporciona
`--session`. No utiliza el identificador heredado del padre (`CODEX_SESSION_ID`)
ni atribuye eventos a la sesión más reciente. Los hooks reconocen los nombres
de herramientas nativos, con namespace y concatenados observados en Codex.
Los identificadores devueltos por spawn se correlacionan solo en su propia sesión.

**Uso reciente**, **instrucciones leídas**, **ejecución observada** y **referencia
observada** describen evidencias distintas. Un comando compuesto, una salida sin
resultado comprobable o una mención a una ruta no se convierten en prueba de
ejecución. Los reportes del agente llevan `source: agent-report`. Los eventos no
prueban que un proceso siga vivo ni que su tarea esté completada.

La instrumentación empieza con esta versión; no reconstruye todos los chats
anteriores. Conserva hasta 10.000 eventos y hasta 1.000 últimos reportes agrupados.
El historial devuelve los 200 eventos más recientes; los últimos reportes de fase
se consultan aparte para sobrevivir a tareas largas.

Una **instalación comprobada** coincide con la firma del árbol de archivos
registrado por Nexo. Si se modifica o desaparece, el panel lo señala. La validación
mostrada comprueba generación, firma y antigüedad del recibo; no vuelve a ejecutar
sus pruebas ni lee los archivos privados originales de evidencia.

Un `learn retry` conserva el fallo original y sus ejecuciones en `learn history`,
mantiene una única observación y no renueva su antigüedad. El panel refleja la
racha corregida e invalida las candidatas que dependían de la evidencia anterior.

## Funcionamiento y pruebas

El registro mínimo de actividad se guarda en
`~/.codex/nexo-kit/activity.sqlite3`. Contiene identificadores, rutas, fechas,
estados y fuentes; no guarda prompts, comandos completos ni salidas completas.
Los hooks mantienen sus comandos y su confianza nativa. Una escritura denegada
se informa y no se fuerza.

El panel consulta tareas y aprendizaje mediante copias temporales acotadas de
SQLite y su WAL, para evitar crear auxiliares en los proyectos. La comprobación
de estabilidad por tamaño y fecha es optimista; ante cambios concurrentes
detectados se informa del error parcial y el siguiente refresco vuelve a
consultar. No es una transacción atómica entre proyectos.

Las pruebas cubren firmas, rachas, WAL concurrente, ausencia de escrituras en
proyectos, clasificación de actividad, errores de acceso, autenticación local,
Origin/Host, límites HTTP, doble apertura y arranque aislado como paquete. Los
fixtures de prueba no cuentan como peticiones del usuario.

El informe de esta ejecución, incluida la comprobación en navegador real, queda
en `.nexo/dashboard-verification.json`.
