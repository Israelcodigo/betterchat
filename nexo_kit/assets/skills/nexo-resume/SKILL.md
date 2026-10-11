---
name: nexo-resume
description: Recupera el trabajo de un proyecto Nexo después de reiniciar o compactar una sesión, reconciliando tareas, worktrees y evidencias actuales.
---

# Recuperar una sesión

Proyecto: {{PROJECT}}. El estado persistido ayuda a reconstruir el trabajo, pero el código y los procesos pueden haber cambiado desde el último checkpoint.

~~~powershell
& {{NEXO_COMMAND}} status
& {{NEXO_COMMAND}} task list
& {{NEXO_COMMAND}} task ready
~~~

## Reconstruir lo necesario

1. Lee el objetivo actual, instrucciones del repositorio y checkpoint existente. Conserva las correcciones posteriores del usuario.
2. Identifica tareas completadas, propuestas en review, trabajo bloqueado y reclamaciones activas. Usa rutas reales de worktree, no una ruta recordada de otra sesión.
3. Comprueba el estado Git de esos checkouts antes de escribir. Una rama o un proceso presente no demuestra que su resultado ya se integró.
4. Recupera solo la evidencia y las memorias relacionadas con la siguiente acción.

~~~powershell
& {{NEXO_COMMAND}} context search 'tarea o error pendiente'
& {{NEXO_COMMAND}} context fetch 'ID_REAL'
& {{NEXO_COMMAND}} memory search 'área pendiente'
~~~

No cargues el historial completo para recrear cada paso. Reconstruye objetivo, decisiones que siguen vigentes, evidencia, bloqueos y siguiente acción.

## Reconciliar antes de continuar

Si un lease ha expirado, utiliza task recover e inspecciona el resultado. Si el propietario sigue trabajando, no te apropies de su tarea. Un token antiguo no se reutiliza tras recuperar una reclamación.

Si cambió el contenido o la configuración de checks desde el reporte, considera obsoleta esa evidencia y vuelve a verificar cuando corresponda. No cambies estados a done por leer un resumen optimista.

Después de reconciliar los hechos, guarda un punto de recuperación:

~~~powershell
& {{NEXO_COMMAND}} checkpoint
~~~

Un checkpoint conserva estado recuperable; no completa tareas, no mata procesos y no integra ramas. Continúa con la siguiente acción autorizada y comunica brevemente qué has recuperado y qué sigue siendo incierto.
