# Contratos que se pueden comprobar

Una tarea es suficientemente concreta cuando otro agente puede decidir qué entregar sin inventar el objetivo y un tercero puede comprobarlo sin confiar en su descripción del éxito.

## Ejemplo: rutas de Windows

Título: Admitir un directorio de proyecto que contenga espacios.

Descripción:

> La CLI debe inicializar y consultar un proyecto dentro de “Mi proyecto”. El caso se ejecuta desde un directorio distinto. Alcance: resolución de rutas e invocación del runtime. Aceptación: status identifica el mismo proyecto y ninguna salida se escribe fuera de él; se conserva una configuración ajena ya existente. Evidencia: prueba de integración con ruta temporal y comparación de la configuración antes/después. No incluye elevar privilegios ni registrar comandos globales.

Evita “mejorar portabilidad”: no delimita plataforma, entrada ni resultado.

## Dependencias

Usa una arista A → B cuando B necesita un resultado de A. No añadas aristas solo para reproducir una lista de pasos. Crear un contrato y sus pruebas puede preceder a dos implementaciones independientes; modificar el mismo esquema y su migración normalmente requiere una sola tarea o una secuencia explícita.

Un predecesor bloqueado o fallido sigue bloqueando a sus consumidores. Una investigación puede estar terminada si entregó una conclusión negativa verificable; esto exige que su contrato fuera investigar, no entregar la funcionalidad ausente.

## Tamaño y paralelismo

Parte una tarea cuando su contrato contenga resultados separables, verificables y con propietarios distintos. Conserva juntas las modificaciones pequeñas que necesitan el mismo contexto o interfaz. Más subagentes no garantizan mayor velocidad: cuenta también integración y revisión.

Cada trabajador recibe objetivo, criterios, límites, evidencia existente y archivos propios. El coordinador conserva dependencias e integración. Un informe de progreso de un trabajador es información que revisar, no permiso para modificar el plan o completar otras tareas.

## Checks

Prefiere comandos existentes del proyecto y pruebas por interfaz pública. Distingue prueba automática, inspección manual y observación en un cliente real. Un check de sintaxis puede demostrar que el archivo se parsea; no demuestra que un host haya cargado el plugin. Un mock de CLI demuestra el contrato del adaptador; no demuestra autenticación ni una respuesta del modelo.
