---
name: nexo-build
description: Implementa una tarea Nexo con alcance definido, registra el trabajo y prepara una propuesta verificable sin confundir salida del agente con tarea completada.
---

# Construir una tarea

Trabaja sobre el contrato de una tarea existente en {{PROJECT}}. Lee su descripción, sus dependencias y las instrucciones del checkout que realmente modificarás.

~~~powershell
& {{NEXO_COMMAND}} task list
& {{NEXO_COMMAND}} task ready
~~~

## Elegir quién ejecuta

Si esta sesión va a implementar directamente una tarea pending y desbloqueada, reclama la tarea:

~~~powershell
& {{NEXO_COMMAND}} task claim 'ID_REAL' --owner 'sesion-actual' --lease-seconds 3600
~~~

Conserva el token devuelto. No inventes ni reutilices un token de otra reclamación. Si un runner ya te asignó la tarea y token, utiliza esa asignación; no lances otro runner ni reclames el mismo trabajo desde dentro del trabajador.

Si el usuario pidió ejecución automática de varias tareas, utiliza nexo-orchestrate. El ejemplo manual reserva una hora; ajusta el plazo al trabajo y renueva antes de vencer si aún eres su dueño:

~~~powershell
& {{NEXO_COMMAND}} task heartbeat 'ID_REAL' --token 'TOKEN_DEVUELTO' --lease-seconds 3600
~~~

La renovación puede ser rechazada si caducó o se perdió la propiedad. En ese caso deja de registrar resultados como dueño y reconcilia el estado; nunca fuerces un token antiguo.

## Implementar

1. Comprueba la reproducción o baseline pertinente antes de cambiar código. Distingue defectos preexistentes de los introducidos por el cambio.
2. Para lógica nueva, regresiones o fronteras frágiles, trabaja con pruebas de comportamiento en incrementos pequeños: una observación que falle y una implementación que la satisfaga. No añadas tests que solo reproduzcan una constante o la estructura privada del código.
3. Conserva el contrato público y el estilo del proyecto. Si un hallazgo obliga a cambiar alcance o dependencias, comunícalo al coordinador antes de modificar archivos asignados a otro trabajador.
4. Ejecuta los checks adecuados, corrige los fallos dentro del presupuesto fijado y conserva comandos y resultados. No debilites un check, el sandbox o los permisos para obtener un resultado favorable.
5. Revisa el diff y elimina cambios incidentales. No borres archivos ni modificaciones de otro trabajador.

Las salidas extensas pueden consultarse por búsqueda y lectura puntual:

~~~powershell
& {{NEXO_COMMAND}} context search 'error concreto'
& {{NEXO_COMMAND}} context fetch 'ID_REAL'
~~~

## Entregar la propuesta

Al terminar una implementación manual, registra una propuesta para revisión:

~~~powershell
& {{NEXO_COMMAND}} task finish 'ID_REAL' --token 'TOKEN_DEVUELTO' --status review
~~~

Si la ejecución falló o falta un insumo externo, registra la causa también en el estado:

~~~powershell
& {{NEXO_COMMAND}} task finish 'ID_REAL' --token 'TOKEN_DEVUELTO' --status failed --error 'Causa observada y siguiente experimento.'
~~~

Usa blocked en lugar de failed cuando falte una dependencia externa. El error persistido permite a un intento posterior recuperar el diagnóstico. Un token rechazado significa que el registro no se aplicó; no intentes forzarlo con cambios directos en SQLite.

Si verify --complete falla, la tarea pasa a failed. Inspecciona el diagnóstico, usa task retry cuando proceda y reclama de nuevo para corregirla; conserva el nuevo token y reutiliza el workspace existente. Un verify sin --complete no cambia el estado por sí solo. No llames task finish con el token de una entrega anterior.

La entrega incluye archivos modificados, criterios satisfechos, checks ejecutados, límites y siguiente paso. El estado review no equivale a done. La verificación y la integración tienen pasos propios; un código de salida cero del trabajador no demuestra completitud.
