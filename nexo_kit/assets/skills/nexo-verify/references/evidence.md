# Comandos y alcance de la evidencia

## Argumentos en Windows

Nexo invoca procesos con un array de argumentos y sin concatenar un shell implícito. Así mantiene separados ejecutable, rutas con espacios y parámetros.

Prefiere un ejecutable real, una ruta absoluta al intérprete del entorno o la forma módulo del lenguaje. Para una herramienta que solo existe como archivo .cmd, puede hacer falta un shell explícito; verifica cómo la expone el proyecto en lugar de inventar un ejecutable equivalente.

Para un script PowerShell existente, la forma conceptual es:

~~~json
["pwsh","-NoProfile","-File","scripts/verify.ps1"]
~~~

Esto presupone que pwsh existe y que la ejecución del script está permitida por la política del entorno. No añadas ExecutionPolicy Bypass para eludirla. PowerShell 5 usa powershell.exe; no es automáticamente equivalente a PowerShell 7.

No introduzcas secretos en argumentos ni logs. Un array evita una clase de errores de quoting, pero no convierte un comando de terceros en seguro ni limita sus efectos.

## Checks que generan archivos

Nexo compara el contenido antes y después de verificar. Los artefactos de build, caches y reportes regenerados deben estar excluidos apropiadamente del contenido fuente que representa el fingerprint. Si el check formatea o modifica código fuente, aplica primero ese cambio y vuelve a ejecutar sobre el resultado estable.

Si se configura JUnit en .nexo/config.json, la ruta es relativa al workspace de la tarea. El parser necesita casos ejecutados, rechaza resultados fallidos y no acepta un archivo que no se haya regenerado. No declares JUnit cuando el runner no lo produce.

## Tres niveles que no se deben mezclar

| Observación | Lo que demuestra | Lo que queda fuera |
|---|---|---|
| Parseo/configuración válida | El archivo tiene estructura aceptada por el validador utilizado | Carga real por el host |
| Proceso simulado + pruebas de CLI | Contrato de argumentos, estados y manejo de fallos | Autenticación, acceso al modelo, calidad de su respuesta |
| Ejecución real en un proyecto de prueba | Comportamiento observado en esa versión y escenario | Todas las versiones, entornos y tareas futuras |

Un reporte útil enumera estas diferencias. Los IDs y hashes permiten relacionar evidencia y contenido; no son una firma criptográfica de un tercero ni una protección frente a un proceso con permiso para reescribir todo el estado.
