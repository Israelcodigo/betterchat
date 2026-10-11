# Selección automática y automejora

Nexo 1.1 añade selección por intención y aprendizaje de procedimientos repetidos.
La instalación personal utiliza las skills en todos los proyectos; las observaciones
de aprendizaje permanecen separadas en `.nexo/learning.sqlite3` de cada proyecto.

## Uso cotidiano

No necesitas escribir `$nexo-plan` ni recordar nombres. Pide el trabajo normalmente.
Las instrucciones globales orientan a Codex hacia diagnóstico, planificación,
implementación, revisión, verificación o recuperación según la tarea. Una skill de
dominio puede ser más adecuada y una tarea pequeña puede resolverse directamente.

Codex permite la selección implícita a partir de la descripción de cada skill.
Es una decisión del agente, no una garantía de que cargará una skill concreta en
cada petición. Véase la [documentación oficial](https://learn.chatgpt.com/docs/build-skills).

## Ciclo de aprendizaje

1. El coordinador detecta un procedimiento útil y reutilizable mientras resuelve
   un encargo real. Busca antes una skill existente que ya lo cubra.
2. Registra una observación con la clave del procedimiento, un ID de petición,
   archivos de evidencia y un comando que compruebe el resultado. Nexo ejecuta
   el comando y registra sus resultados y hashes.
3. Tres peticiones distintas satisfactorias, dentro de los últimos 90 días,
   permiten preparar una candidata.
   Reintentos y subtareas conservan el mismo ID; no cuentan como repeticiones.
   Un fallo interrumpe la racha.
4. Codex escribe un procedimiento concreto con `skill-creator`, valida su formato,
   lo aplica a un caso nuevo y revisa el resultado. La frecuencia por sí sola
   no acredita que una instrucción sea correcta.
5. El runtime vincula la validación al contenido completo de la candidata, sus
   artefactos y el log. Una edición posterior invalida ese recibo.
   Una validación de más de 90 días debe repetirse antes de instalar.
6. Con la automejora autorizada, instala la skill sin pedir otra confirmación.
   Informa brevemente de su nombre, alcance y comprobación.

Las skills específicas de un repositorio van en `.agents/skills` de ese proyecto.
Las portables, sin rutas particulares ni datos privados, pueden ir en
`$CODEX_HOME/skills`. Las observaciones no se agregan entre proyectos: tres
peticiones repartidas entre tres proyectos no se convierten silenciosamente en
tres éxitos de un único procedimiento.

El instalador conserva skills ajenas y modificaciones del usuario. Las actualizaciones
de sus propias skills requieren comprobar los hashes y conservan un respaldo.
Si un permiso del cliente impide instalar, la candidata permanece disponible y se
informa del bloqueo. La autorización de aprendizaje no autoriza nuevas publicaciones,
envíos, despliegues ni cambios de permisos.

## Preferencia personal

En este perfil el usuario ha solicitado activar la automejora. Una instalación
nueva del kit conserva por defecto la selección automática y deja la automejora
desactivada hasta que se autorice. La preferencia persiste en
`~/.codex/nexo-kit/preferences.json` y sobrevive a reinstalaciones normales.

Desde la carpeta del código fuente, con el Python persistente usado al instalar:

```powershell
& $nexoPython .\nexo.py personal install --auto-learn
& $nexoPython .\nexo.py personal preferences
# Para detener futuras creaciones automáticas, conservando skills y registros:
& $nexoPython .\nexo.py personal install --no-auto-learn
```

Abre un chat nuevo después de cambiar estas instrucciones. Codex puede detectar
skills nuevas, pero una sesión existente puede conservar su catálogo anterior.

## Límites concretos

El aprendizaje lo realiza Codex mientras atiende tus tareas, siguiendo las
instrucciones globales y `nexo-learn`. No hay un proceso que lea todos tus chats,
recupere retrospectivamente todas tus peticiones o modifique skills cuando Codex
está cerrado. Los hooks actuales no cuentan peticiones del usuario.

Los ensayos de integración usan directorios desechables con herencia normal de
permisos, sin modificar ACLs existentes. El agente debe distinguir instalación
en disco de carga por el cliente e informar de cualquier limitación. Las pruebas
históricas de otros proyectos no se presentan como evidencia de esta entrega.

El runtime acredita ejecución de checks, hashes y repetición de IDs distintos;
la calidad del check, la semejanza entre peticiones y la utilidad de la skill
requieren juicio del agente. No es entrenamiento del modelo. No se promueven
automáticamente memorias ni preferencias personales inferidas.

La prueba real y las pruebas de regresión de esta versión se documentan en
[VERIFICATION.md](VERIFICATION.md).
