# Historias de usuario y validaciones funcionales

Estas historias describen qué debe permitir o rechazar el sistema según el backend actual. Las validaciones están escritas para que análisis, desarrollo y QA puedan leerlas sin conocer la implementación. Cuando una función todavía no existe, se indica expresamente.

## HU07 — Importar planteles de una competición anterior

**Descripción.** Como administrador, quiero reutilizar el plantel histórico de equipos que participaron en una competición anterior para preparar los planteles de una competición actual, sin modificar los datos de la competición de origen.

**Validaciones.**

- Solo un administrador puede consultar planteles importables y guardar cambios en los participantes.
- La competición de origen y la de destino deben existir y ser distintas.
- La competición de origen debe estar finalizada.
- Ambas competiciones deben corresponder al mismo deporte y género.
- La competición de destino debe estar habilitada y no debe tener partidos generados.
- Solo se ofrecen equipos que ya forman parte tanto de la competición de origen como de la de destino.
- Se ofrece el plantel histórico completo de cada equipo. No se importan jugadores sueltos ni una parte del plantel.
- Un equipo no se ofrece si está deshabilitado o si ya no corresponde al deporte o género de la competición de destino.
- Un plantel no se ofrece si no cumple la cantidad mínima y máxima de jugadores establecida para su deporte.
- Si un solo jugador del plantel histórico está deshabilitado, ya no pertenece al equipo, no coincide en deporte o género, o tiene una sanción activa, se excluye el plantel completo.
- Si no hay planteles que cumplan las reglas, se muestra una lista vacía. Esto no constituye un error.
- Elegir un plantel para importar solo prepara la selección en el formulario. No guarda cambios por sí solo. Volver a elegir un plantel para el mismo equipo reemplaza la selección anterior de ese equipo, sin duplicarla.
- Al guardar, se envía la selección completa de equipos, jugadores y árbitros. El sistema vuelve a comprobar la validez de todos ellos aunque el plantel hubiera aparecido entre las opciones importables.
- La competición debe conservar su cantidad de equipos. Debe tener entre 4 y 16, en número par, sin equipos repetidos y con un árbitro distinto por cada dos equipos.
- Cada equipo y jugador seleccionado debe existir, estar habilitado y coincidir con el deporte y género de la competición. Cada árbitro seleccionado debe existir y tener el rol de árbitro.
- Cada plantel debe respetar el mínimo y máximo de jugadores del deporte. Un jugador debe seguir perteneciendo al equipo elegido, no puede repetirse dentro del plantel ni representar a dos equipos en la misma competición.
- Un jugador con sanción activa no puede incorporarse al plantel de la competición.
- Si alguna validación falla, no se guarda ninguna parte del cambio y se informa el motivo.
- Importar planteles no crea equipos, jugadores o árbitros, no copia sanciones y no modifica el historial de la competición de origen.

**Punto a corregir si se exige conservar los mismos equipos.** El backend actual impide cambiar la cantidad de equipos al guardar, pero no impide cambiar sus identificadores. La consulta de importación sí limita las opciones a equipos presentes en ambas competiciones.

## HU — Escanear un equipo y consultar sus escaneos

**Descripción.** Como administrador o árbitro asignado, quiero enviar una foto grupal de uno de los equipos del partido para identificar a los jugadores y conocer si figuraban en el plantel histórico y si tenían sanciones aplicables.

**Validaciones.**

- Solo puede crear un escaneo un administrador o el árbitro asignado al partido.
- Un administrador puede escanear cualquiera de los dos equipos del partido. Un árbitro solo puede escanear partidos que tiene asignados.
- El equipo seleccionado debe ser uno de los dos que disputan ese partido.
- El partido debe estar en curso o haber finalizado hace como máximo dos horas. Se rechazan partidos incompletos, programados, futuros o finalizados fuera de esa tolerancia.
- Se debe enviar exactamente una foto. Se rechazan las solicitudes sin foto, con fotos repetidas o con datos adicionales.
- La foto debe ser realmente PNG o JPEG, no estar dañada, pesar como máximo 10 MiB, medir entre 320 y 4096 píxeles por lado y no superar los 12 millones de píxeles.
- La foto debe contener al menos un rostro detectable. Todos los rostros detectados deben medir al menos 160 × 160 píxeles. Si no hay rostros, la imagen está dañada o algún rostro es demasiado pequeño, se informa el motivo y no se guarda el escaneo.
- La búsqueda de identidad se limita a jugadores habilitados que actualmente pertenecen al equipo seleccionado y tienen al menos una foto base. Nunca se buscan coincidencias en otros equipos ni en todos los jugadores del deporte.
- Un jugador sin fotos base no provoca por sí solo un error del servicio: sencillamente no puede ser reconocido automáticamente. Si una foto base registrada no puede leerse, se informa una falla de procesamiento y no se guarda ese intento.
- Para identificar a un jugador, su foto debe ser la mejor coincidencia, con una distancia no mayor que 0,60 y una ventaja de al menos 0,05 frente al segundo candidato. Si tiene varias fotos base, se usa su mejor coincidencia.
- Un rostro sin candidato válido, con una coincidencia ambigua o que apunta a un jugador ya asociado a otro rostro de la misma foto queda como desconocido.
- Un rostro reconocido que figura en el plantel histórico y no tenía sanciones aplicables queda como «declarado y habilitado» (declared_eligible).
- Un rostro reconocido que figura en el plantel histórico y tenía sanciones aplicables queda como «declarado y sancionado» (declared_sanctioned).
- Un rostro reconocido que pertenece actualmente al equipo, pero no figuraba en su plantel histórico y no tenía sanciones aplicables, queda como «miembro no declarado» (team_member_undeclared).
- Si ese miembro no declarado tenía sanciones aplicables, queda como «miembro no declarado y sancionado» (team_member_undeclared_sanctioned).
- Un rostro no identificado queda como «desconocido» (unknown). Esos cinco son los únicos estados posibles.
- Cada rostro conserva su posición en la foto. Sus coordenadas no pueden ser negativas y la región marcada debe tener ancho y alto mayores que cero.
- Un rostro reconocido debe tener un jugador y un porcentaje de coincidencia entre 0 y 100. Un rostro desconocido no puede tener jugador, porcentaje ni sanciones asociadas.
- El porcentaje es una representación de la distancia entre rostros; no debe presentarse como una probabilidad de identidad.
- Las sanciones se evalúan según lo que correspondía inmediatamente antes del inicio del partido escaneado. No se usa simplemente el estado actual de la sanción al consultar el historial.
- Una sanción creada durante ese mismo partido o que entró en vigencia después de su inicio no inhabilita al jugador para ese partido.
- Si un partido consumió después una fecha de sanción, el escaneo debe seguir mostrando la situación que tenía el jugador cuando lo disputó. También se conserva esa evaluación si el jugador fue deshabilitado más tarde.
- Al evaluar sanciones se respetan los equipos asociados originalmente a cada sanción, los partidos con horarios superpuestos, el orden de cumplimiento de sanciones y los límites estrictos de tiempo.
- Se conservan todas las sanciones que correspondían al partido, con el título, motivo y cantidad de fechas restantes que tenían en ese momento. Una misma sanción no puede repetirse dentro del resultado de un rostro.
- Consultar la elegibilidad de un jugador durante el escaneo no consume fechas de sanción ni cambia sus contadores o estados.
- Se informa la cantidad esperada de jugadores según el máximo en cancha del deporte, la cantidad de rostros detectados y la cantidad de jugadores distintos reconocidos.
- Si se reconocen menos o más jugadores distintos de los esperados, se muestra una advertencia que no bloquea el escaneo. El sistema no inventa ni nombra a los supuestos faltantes o sobrantes.
- Cada escaneo válido se guarda completo como historial que no se modifica después. Una falla de procesamiento, base de datos o almacenamiento no debe dejar resultados ni archivos parciales.
- La foto grupal se conserva únicamente cuando al menos un rostro queda desconocido. No se conserva una foto grupal asociada a un escaneo cuyos rostros fueron todos identificados.
- No se copian fotos base de jugadores al historial. El resultado tampoco entrega imágenes, nombres internos de archivo, distancias de comparación ni datos biométricos utilizados durante el procesamiento.
- Un administrador puede consultar cualquier historial de escaneos. Un árbitro solo puede consultar los de partidos que tiene asignados.
- Al consultar el historial de un equipo, se valida que ese equipo haya disputado el partido.
- El historial se ordena del escaneo más reciente al más antiguo y se presenta en páginas de 25. Una página fuera de rango devuelve una lista vacía con los totales correctos.
- Cada resultado del historial muestra las cantidades, la advertencia de cantidad cuando corresponda y, para cada rostro, su posición, estado, jugador identificado y porcentaje si existen, y las sanciones que correspondían en ese momento.

**Aclaración.** El escaneo se guarda al enviar una foto válida. No hay una vista previa sin guardar ni un paso posterior de confirmación. La orientación horizontal de la cámara es una tarea de interfaz.

## HU — Conservar fotos grupales con rostros desconocidos

**Descripción.** Como árbitro asignado, quiero que se conserve la foto grupal cuando el escaneo tenga rostros no identificados, para contar con evidencia de esos casos.

**Validaciones disponibles.**

- Si el escaneo termina con al menos un rostro desconocido, se conserva una foto grupal privada vinculada a ese escaneo.
- Si todos los rostros fueron identificados, se guarda el resultado del escaneo y se descarta la foto grupal.
- Una foto grupal no puede conservarse como evidencia de un escaneo sin rostros desconocidos.
- La foto debe cumplir todas las validaciones de formato, tamaño, dimensiones y calidad de la HU de escaneo.
- Una foto rechazada no genera historial ni archivo. Una falla al guardar tampoco debe dejar una foto o un resultado parcial.
- El historial permite saber cuáles rostros quedaron desconocidos, pero no entrega la foto grupal ni permite descargarla.

**Pendiente de definición y desarrollo.** No existen vista previa sin guardar, botón de confirmación, revisión en estado pendiente/resuelta/vencida, marca de auditoría en el partido, descarga autorizada ni eliminación automática de la foto. Antes de convertirlos en validaciones para desarrollo hay que definir la revisión y la retención de la evidencia. Las dos horas de la HU anterior limitan cuándo se permite iniciar un escaneo; no son una fecha de vencimiento de la foto guardada.

## HU12 — Validar manualmente rostros no identificados

**Descripción.** Como árbitro asignado a un partido, quiero revisar los rostros que el escaneo dejó sin identificar y decidir si corresponden a un jugador del equipo, a una persona ajena al plantel o a un caso que no puedo resolver.

**Información relevante.** Hoy el backend conserva cada rostro desconocido con su posición, sin jugador ni porcentaje asignado. Un administrador o el árbitro asignado pueden consultar el resultado, pero todavía no existe una operación para resolverlo. El escaneo original debe permanecer inmutable; las decisiones manuales requerirán un registro separado.

**Validaciones propuestas para implementar esta HU.**

- Solo el árbitro asignado puede confirmar decisiones manuales de ese partido. Un árbitro ajeno no puede hacerlo. El administrador hoy puede consultar el escaneo; si también podrá resolverlo debe decidirse expresamente.
- Solo se pueden resolver rostros que quedaron como desconocidos en un escaneo existente. No se modifica la identidad ni el estado de rostros reconocidos automáticamente.
- La validación manual se propone mientras el partido está en curso o antes de que transcurran dos horas desde su finalización. Al cumplirse ese plazo, la revisión debe quedar vencida y no aceptar decisiones.
- Cada rostro desconocido debe recibir una sola decisión: asignar un jugador, ignorar a una persona ajena al plantel o marcar el caso como no concluyente.
- Para asignar un jugador, este debe pertenecer al equipo del escaneo y no estar asociado a otro rostro del mismo escaneo, ya sea por reconocimiento automático o por otra decisión manual.
- Una asignación manual no tiene porcentaje de coincidencia. Se registra que fue manual, quién la confirmó y cuándo.
- Al asignar un jugador, su situación disciplinaria se evalúa para ese partido con las mismas reglas históricas del escaneo automático. La consulta de elegibilidad no descuenta fechas ni modifica sanciones.
- Al elegir «Ignorar» o «No concluyente», el rostro permanece sin jugador. Ambas decisiones deben distinguirse y conservar quién las tomó y cuándo.
- Las decisiones de todos los rostros desconocidos de un mismo escaneo se confirman juntas. Si falta una, se repite un rostro, se asigna dos veces un jugador o falla cualquier validación, no se guarda ninguna decisión.
- Una revisión ya resuelta o vencida no admite nuevas decisiones. Al confirmar todas las decisiones, la revisión debe pasar a resuelta; la marca de auditoría del partido, si se implementa en la HU de revisión, debe actualizarse en consecuencia.
- La resolución manual no reescribe el resultado histórico del reconocimiento, sus porcentajes ni las sanciones capturadas en ese escaneo.

**Criterios de aceptación para desarrollar HU12.** Estos escenarios describen el resultado esperado; ninguno está implementado todavía.

1. **Asignar un jugador.** Dado un rostro desconocido de un escaneo y un jugador del equipo que no está asociado a otro rostro de ese mismo escaneo, cuando el árbitro asignado lo selecciona y confirma la revisión, entonces se registra una asignación manual separada del resultado original, con el jugador, el árbitro y la fecha, sin porcentaje de coincidencia.
2. **Evaluar la sanción del jugador asignado.** Dado un jugador asignado manualmente, cuando se confirma la revisión, entonces se informa si tenía una sanción aplicable al inicio de ese partido según las mismas reglas que utiliza el escaneo automático. Esta evaluación no descuenta fechas ni altera la sanción.
3. **Ignorar una persona ajena.** Dado un rostro desconocido que no corresponde a un jugador, cuando el árbitro elige «Ignorar» y confirma, entonces el rostro queda sin jugador y se registra esa decisión con autor y fecha.
4. **Caso no concluyente.** Dado un rostro cuya identidad no puede determinarse, cuando el árbitro elige «No concluyente» y confirma, entonces el rostro queda sin jugador y se registra esa decisión final, distinta de «Ignorar», con autor y fecha.
5. **Evitar asignaciones duplicadas.** Dado un jugador ya asociado a un rostro por reconocimiento automático o por otra decisión manual del mismo escaneo, cuando se intenta asignarlo a un segundo rostro, entonces se rechaza la confirmación completa y no se guarda ninguna decisión de ese envío.
6. **Confirmación completa.** Dado un escaneo con varios rostros desconocidos, cuando el árbitro envía exactamente una decisión válida para cada uno, entonces se guardan todas juntas y la revisión pasa a resuelta. Si falta un rostro, se repite uno o alguna decisión es inválida, no se guarda ninguna.
7. **Permisos y plazo.** Dado un árbitro no asignado, un partido que aún no está en curso, una revisión ya resuelta o una revisión cuyo plazo de dos horas desde el fin del partido se cumplió, cuando se intenta confirmar decisiones, entonces se rechaza la operación sin cambiar el escaneo ni las decisiones previas.
8. **Historial inmutable.** Dado un escaneo resuelto manualmente, cuando se vuelve a consultar el resultado automático, entonces sus rostros, porcentajes y sanciones originales permanecen iguales; las decisiones manuales se consultan por separado.

**Decisiones de análisis pendientes.** El backend aún no tiene revisión, estados de revisión, planilla de presencia, marca «Requiere auditoría biométrica» ni descarga de la foto. Antes de desarrollar esta HU hay que definir: qué jugadores aparecen para asignación (plantel histórico, membresía actual o ambos); si asignar equivale a «presente» aun cuando el jugador esté sancionado o no declarado; qué ocurre con la imagen al resolver o vencer la revisión; si el administrador también puede resolver; y qué efecto tienen «Ignorar» y «No concluyente» sobre el inicio o cierre del partido. No debe prometerse que estas acciones actualizan una planilla o retiran una marca que todavía no existen.

## HU — Registrar y gestionar sanciones

**Descripción.** Como administrador o árbitro asignado, quiero registrar una suspensión a un jugador de un partido para conservar su historial disciplinario y controlar futuras incorporaciones.

**Validaciones.**

- Una sanción solo puede crearse desde un partido en curso.
- Puede crearla un administrador o el árbitro asignado a ese partido. Un árbitro ajeno al partido no puede hacerlo.
- El jugador debe existir y figurar en el plantel histórico de uno de los dos equipos del partido. Puede estar deshabilitado actualmente si pertenecía a ese plantel histórico.
- El título es obligatorio, no puede quedar vacío después de quitar espacios al principio y al final, y admite hasta 30 caracteres.
- El motivo sigue la misma regla y admite hasta 250 caracteres.
- La cantidad de fechas debe ser un número entero entre 1 y 10. No se aceptan decimales, valores lógicos ni datos adicionales.
- Si falla una validación, no se registra una sanción parcial.
- La sanción nace activa, con igual cantidad de fechas asignadas y restantes, y conserva quién la creó, el partido y la competición de origen.
- La sanción entra en vigencia al terminar el partido de origen. Ese partido nunca descuenta una fecha.
- Al crearla queda fijo el conjunto de equipos a los que pertenecía el jugador más su equipo del plantel histórico del partido. Los cambios posteriores de membresía o habilitación no alteran ese conjunto.
- La sanción es global: puede cumplirse en partidos de esos equipos aunque se disputen en otras competiciones.
- Solo cuentan los partidos que comienzan estrictamente después de la entrada en vigencia de la sanción y ya terminaron.
- Un grupo de partidos con horarios superpuestos, incluso si se superponen en cadena, descuenta una sola fecha. Los partidos que solo se tocan en el horario cuentan como grupos separados.
- El consumo no depende de que el jugador haya asistido, figurado en un plantel posterior o siga habilitado. Tampoco depende de que sus equipos sigan habilitados.
- Cuando un jugador tiene varias sanciones, se cumple primero la más antigua. Una sanción posterior espera su turno y el grupo que completó la anterior no descuenta también la siguiente.
- Cada descuento se conserva en el historial de cumplimiento y no se cuenta dos veces.
- Al llegar las fechas restantes a cero, la sanción pasa a cumplida y se registra cuándo ocurrió.
- Mientras el jugador tenga al menos una sanción activa, no puede sumarse a un equipo nuevo, volver a uno del que salió ni incorporarse a un plantel de competición. Puede conservar o dejar sus equipos actuales.
- Una sanción cumplida o anulada no bloquea nuevas incorporaciones. Las asociaciones y planteles históricos no se restauran ni se reescriben automáticamente.
- Solo las sanciones activas pueden editarse. Un administrador puede editar cualquiera; el árbitro que la creó solo puede hacerlo mientras el partido de origen siga en curso.
- El título y motivo pueden cambiar mientras la sanción siga activa, incluso después de haber consumido algunas fechas.
- La cantidad de fechas solo puede cambiar antes del primer descuento. Después puede enviarse el mismo valor, pero cualquier cambio de duración se rechaza.
- Editar una sanción no permite cambiar el jugador, partido de origen, equipos asociados, fecha de vigencia, estado ni historial de cumplimiento. Se conserva quién hizo la modificación y cuándo.
- Solo un administrador puede anular una sanción activa, aunque ya haya consumido algunas fechas. Una sanción cumplida o anulada no puede anularse.
- Al anularla se conservan las fechas restantes y los descuentos ya registrados, se registra quién la anuló y cuándo, y se detienen futuros descuentos.
- Anular una sanción libera nuevas incorporaciones solo si el jugador no tiene otra sanción activa.
- Las sanciones no se eliminan físicamente; permanecen en el historial como activas, cumplidas o anuladas.

## HU — Consultar las sanciones de un jugador

**Descripción.** Como administrador o árbitro, quiero consultar el historial de sanciones de un jugador y sus fechas restantes para conocer su situación disciplinaria antes de un encuentro.

**Validaciones.**

- Un administrador y un árbitro autenticados pueden consultar el historial de cualquier jugador y el detalle de una sanción.
- Un jugador deshabilitado conserva y permite consultar su historial.
- Antes de mostrar las sanciones, el sistema actualiza sus fechas restantes y estados según los partidos que ya terminaron.
- El historial incluye sanciones activas, cumplidas y anuladas, salvo que se aplique un filtro de estado.
- Se puede filtrar por estado y por intervalo de fecha de creación. Los extremos del intervalo están incluidos y deben indicar zona horaria. Se rechaza un intervalo cuya fecha final sea anterior a la inicial.
- El historial se ordena de la sanción más reciente a la más antigua. Cada página contiene hasta 25 registros.
- Si una página queda fuera de rango, se devuelve una lista vacía y los totales de paginación correctos.
- Si el jugador nunca recibió sanciones, se devuelve una lista vacía. La interfaz puede mostrar «Sin registros disciplinarios».
- Un jugador o una sanción inexistentes se informan como recursos no encontrados.
- Cada sanción muestra jugador, título, motivo, fechas asignadas y restantes, estado, partido y competición de origen, equipos asociados al crearla, creador, último actualizador, anulador si existe y fechas relevantes.
- La consulta no muestra correos, tokens, imágenes, datos biométricos ni los registros internos de cada descuento.
- Consultar sanciones no autoriza a modificarlas: la edición y anulación siguen las reglas de la HU anterior.

## Validaciones comunes de entrada

- Las acciones protegidas requieren iniciar sesión y respetar el rol o la asignación al partido.
- Los identificadores deben ser válidos y positivos. Los parámetros obligatorios deben estar presentes; las páginas deben ser números positivos.
- Se rechazan datos faltantes, campos desconocidos, tipos incorrectos y solicitudes malformadas, indicando el motivo sin alterar los datos existentes.
- Ante fallas de persistencia, los cambios de una operación se guardan completos o se revierten completos.
