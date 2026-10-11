---
name: nexo-debug
description: Diagnostica un fallo reproducible de software o del runner Nexo mediante hipótesis contrastables, evitando reintentos ciegos y conservando la evidencia.
---

# Diagnosticar un fallo

Parte del síntoma observado en {{PROJECT}}, del resultado esperado y de la última ejecución. Si el fallo está en el kit, consulta primero:

~~~powershell
& {{NEXO_COMMAND}} doctor
& {{NEXO_COMMAND}} status
& {{NEXO_COMMAND}} context search 'mensaje de error'
~~~

Obtén detalles con context fetch sobre un ID devuelto. Lee solo el segmento necesario; no vuelvas a inyectar todo un log grande.

## Ciclo de investigación

1. Reproduce el caso más pequeño útil en el entorno donde falló. Registra versión, directorio, entrada y salida. Si no se reproduce, compara esas condiciones antes de cambiar código.
2. Formula una hipótesis que pueda refutarse y un experimento que distinga esa causa de una alternativa. Cambia una variable relevante cada vez.
3. Comprueba límites de proceso, rutas, permisos, estado persistido y dependencias si afectan al síntoma. En Windows distingue intérprete ausente, script .cmd, quoting de rutas y conflicto de archivos abiertos.
4. Corrige la causa demostrada. Añade una regresión en una interfaz pública cuando sea proporcional al riesgo.
5. Ejecuta la reproducción y los checks de las rutas afectadas. Informa si el fallo anterior desapareció y qué comportamiento sigue sin verificarse.

## Presupuesto y recuperación

Antes de un bucle de reparación automática, fija un número de intentos y qué señal cuenta como progreso. Cuando se repita el mismo fallo sin nueva evidencia, interrumpe el bucle y conserva diagnóstico y siguiente experimento. Un límite agotado significa trabajo incompleto, no permiso para ignorar el error.

Un lease expirado o una tarea fallida son condiciones de coordinación, no pruebas de un defecto en el producto. Tras identificar y resolver la causa, usa las operaciones explícitas de recuperación:

~~~powershell
& {{NEXO_COMMAND}} task recover
& {{NEXO_COMMAND}} task retry 'ID_REAL'
~~~

Inspecciona el resultado de cada comando; no resetees toda la base de datos ni borres worktrees como solución general. No relances un proceso que sigue activo bajo otro dueño.

Si queda conocimiento reutilizable que no es evidente en el código o los tests finales, registra una memoria candidata con la evidencia. Un intento fallido sin causa demostrada se conserva como hipótesis, no como regla.
