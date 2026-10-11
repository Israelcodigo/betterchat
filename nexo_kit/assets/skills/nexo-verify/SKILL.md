---
name: nexo-verify
description: Ejecuta checks Nexo y vincula sus resultados al contenido actual, detectando evidencia ausente u obsoleta antes de completar una tarea.
---

# Verificar con evidencia

La verificación determina qué se ha demostrado sobre {{PROJECT}}. Empieza leyendo el contrato y los checks ya configurados en .nexo/config.json. Comprueba que el directorio verificado es el checkout de la propuesta.

## Configurar solo los checks necesarios

Reutiliza comandos del proyecto. Añade un check cuando cubra un criterio que aún no tenga prueba:

~~~powershell
& {{NEXO_COMMAND}} check add 'pruebas' --command-json '["{python}","-B","-m","unittest","discover","-s","tests"]'
~~~

El ejemplo sirve para un proyecto que ya tiene tests compatibles con unittest. Sustituye el comando por el ejecutable real del proyecto. El valor es un array JSON de argumentos, no una cadena para el shell. Lee [comandos y alcance de la evidencia](references/evidence.md) para Windows, JUnit y checks que generan archivos.

No registres un comando vacío, un echo de éxito o un test que siempre pasa para desbloquear done. Si no hay check automático apropiado, conserva el estado sin completar y explica la comprobación manual realizada y lo que todavía falta.

## Ejecutar

Proyecto actual, sin transición de tareas:

~~~powershell
& {{NEXO_COMMAND}} verify
~~~

Propuesta de tarea:

~~~powershell
& {{NEXO_COMMAND}} verify --task 'ID_REAL'
~~~

Después de revisar los resultados, completar una tarea que se encuentra en review:

~~~powershell
& {{NEXO_COMMAND}} verify --task 'ID_REAL' --complete
~~~

La última orden vuelve a ejecutar la verificación; usa el contenido y configuración actuales. No la repitas innecesariamente: ejecuta directamente con --complete cuando esa finalización ya sea el paso autorizado y adecuado.

## Cerrar una revisión o auditoría

Un entregable de auditoría puede completarse con defectos documentados que aún
no se han corregido. Su contrato es reproducir, explicar y priorizar los hallazgos,
con fuentes, criterios de aceptación y límites explícitos. Los tests generales
del producto no prueban por sí solos que ese informe cumpla el contrato.

Conserva el informe y las reproducciones como artefactos del proyecto, añade un
check que compruebe sus resultados y su integridad sin modificar la evidencia,
y revisa las partes que requieren juicio humano. No rebajes los checks del
proyecto para cerrar una tarea. Después usa --task ID --complete y comprueba el
estado. Si no hay evidencia suficiente, deja review y explica qué queda pendiente.

Antes de la respuesta final consulta status. Informa de las tareas de este
encargo que sigan en review, failed o blocked. Un verify sin --complete devuelve
un aviso de tareas pendientes, pero no las finaliza ni reemplaza sus recibos.

## Interpretar

- Un check fallido o timeout impide concluir éxito. Con --complete la tarea pasa a failed: inspecciona el diagnóstico y usa task retry antes de reclamar un intento de reparación. Sin --complete se produce un informe sin esa transición.
- Checks vacíos no demuestran completitud.
- Un fingerprint cambiado durante o después de la ejecución vuelve obsoleta la evidencia.
- Un informe JUnit anterior no se puede reutilizar como prueba de la ejecución actual.
- Si cambias código o configuración de checks, verifica el resultado final otra vez.

Reporta criterios demostrados, checks ejecutados, evidencia guardada y límites. Los checks configurados cubren exactamente lo que comprueban; no garantizan ausencia universal de defectos. Un test del adaptador con un proceso simulado no demuestra uso real de Claude Code o Codex autenticado.
