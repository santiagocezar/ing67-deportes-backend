# Historias de usuario — versión consolidada

Las validaciones corregidas de HU07 y HU09 se conservan del texto recibido. En HU11 a HU14, «Información relevante» se reemplaza por las validaciones del documento anterior. Los criterios de aceptación se redactan como escenarios «Dado/Cuando/Entonces» y las tareas describen responsabilidades generales.

**Estado del alcance:** HU11 conserva imágenes con rostros desconocidos, pero no implementa revisión, descarga ni vencimiento. HU12 describe una funcionalidad pendiente; sus criterios requieren cerrar las decisiones de análisis indicadas en sus tareas.

## HU07: Importación de planteles de temporadas anteriores

**Descripción:** Como administrador, quiero consultar planteles históricos elegibles de una competición finalizada y usar sus jugadores para preparar los planteles de equipos ya inscritos en otra competición, sin alterar los datos históricos de origen.

**Validaciones:**

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

**Criterios de aceptación**

Dado un administrador que edita una competición habilitada sin partidos, cuando elige como origen una competición distinta, finalizada y del mismo deporte y género, entonces el sistema muestra únicamente los planteles completos de equipos que participan en ambas competiciones y siguen siendo elegibles.

Dado que un equipo o alguno de los jugadores de su plantel histórico ya no cumple las reglas de habilitación, pertenencia, deporte, género, tamaño del plantel o sanciones, cuando se consultan las opciones, entonces ese plantel completo no se ofrece. Si no hay opciones elegibles, se muestra una lista vacía.

Dado que el administrador selecciona un plantel ofrecido, cuando lo incorpora al formulario, entonces se reemplaza la selección temporal de jugadores de ese equipo. Los demás equipos y árbitros conservan su selección, y repetir la importación para el mismo equipo no genera duplicados.

Dado un formulario completo que cumple las reglas de equipos, jugadores, planteles y árbitros, cuando el administrador guarda los participantes, entonces se sustituyen todos juntos. La competición de origen, sus planteles históricos y las sanciones existentes permanecen intactos.

Dado que la cantidad de equipos cambia, no está entre 4 y 16 o es impar, o que faltan árbitros o se repite alguno, cuando se intenta guardar, entonces se rechaza toda la operación y se informa la regla incumplida.

Dado que un equipo, jugador o árbitro no existe o no cumple sus reglas, que un jugador aparece en dos equipos, o que una sanción se activó después de consultar las opciones, cuando se intenta guardar, entonces el sistema vuelve a validar la selección y no persiste cambios parciales.

Dado un origen no finalizado o incompatible, un destino deshabilitado o con partidos, o un usuario sin permiso de administrador, cuando se intenta consultar opciones o guardar la importación, entonces se rechaza la acción sin modificar ninguna competición.

**Tareas:**

- **Backend:** Mantener la consulta de planteles elegibles y el guardado integral de participantes con sus reglas de negocio.
- **Frontend:** Incorporar la selección de origen y planteles al flujo de edición de participantes, mostrando estados de carga, ausencia de opciones y errores.
- **QA:** Preparar y ejecutar casos sobre elegibilidad, permisos, duplicados, cambios entre consulta y guardado, y reversión de operaciones fallidas.
- **Analista:** Confirmar el alcance de la importación y el comportamiento visible de la selección y los rechazos.

## HU09: Identificación automática de jugadores

**Descripción:** Como administrador o árbitro asignado, quiero escanear una foto grupal de uno de los equipos del partido para identificar a los jugadores presentes y conocer si figuraban en su plantel histórico y si tenían sanciones aplicables.

**Validaciones:**

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

**Criterios de aceptación**

Dado un administrador o el árbitro asignado, cuando envía una foto de uno de los dos equipos de un partido en curso o finalizado hace como máximo dos horas, entonces el sistema procesa el escaneo. Si el partido está fuera de esa ventana, el equipo no participa o el árbitro no está asignado, se rechaza la solicitud.

Dado que se envía una foto válida en PNG o JPEG, dentro de los límites de tamaño y dimensiones y con rostros detectables de tamaño suficiente, cuando se procesa, entonces se registra el resultado de cada rostro. Si la foto falta, está dañada, excede los límites, no contiene rostros o alguno no tiene la calidad mínima, se informa el motivo y no se crea historial.

Dado un rostro con una coincidencia única que cumple el límite de distancia y la diferencia mínima frente al segundo candidato, cuando se compara con las fotos base de jugadores habilitados del equipo elegido, entonces se identifica al jugador. No se buscan candidatos en otros equipos.

Dado un jugador identificado, cuando se evalúa su presencia en el plantel histórico y las sanciones que le correspondían antes de iniciar ese partido, entonces el rostro recibe el estado que corresponde: declarado y habilitado, declarado y sancionado, miembro no declarado o miembro no declarado y sancionado.

Dado un rostro sin candidato válido, ambiguo o que coincide con un jugador ya asignado a otro rostro, cuando termina la comparación, entonces queda como desconocido, sin jugador ni porcentaje. La ausencia de fotos base de un jugador no impide procesar a los demás.

Dado que una sanción comenzó durante el mismo partido, se cumplió después o el jugador fue deshabilitado posteriormente, cuando se consulta el escaneo, entonces se conserva la evaluación histórica del momento del partido. Consultar esa elegibilidad no descuenta fechas ni cambia sanciones.

Dado que la cantidad de jugadores distintos reconocidos difiere de la cantidad esperada en cancha, cuando se entrega el resultado, entonces se muestra una advertencia que no bloquea el escaneo ni inventa identidades faltantes o sobrantes.

Dado un escaneo válido, cuando termina el procesamiento, entonces queda guardado completo e inmutable, con cantidades, ubicaciones de rostros, estados, jugadores reconocidos y sanciones aplicables. La foto grupal se conserva únicamente si hay algún rostro desconocido; en caso contrario se descarta.

Dado un administrador o el árbitro asignado, cuando consulta el historial de escaneos de un equipo del partido, entonces ve los resultados del más reciente al más antiguo, paginados. Una página fuera de rango devuelve una lista vacía con los totales correctos, y otro árbitro no puede acceder.

Dada una falla de procesamiento, base de datos o almacenamiento, cuando se intenta completar el escaneo, entonces se informa el fallo y no quedan resultados ni archivos parciales. El historial nunca muestra fotos base, imágenes grupales, referencias privadas ni datos biométricos de comparación.

**Tareas:**

- **Backend:** Mantener el procesamiento, la clasificación, la persistencia y la consulta protegida de escaneos.
- **Frontend:** Integrar la captura y el envío de la foto, y mostrar resultados, advertencias e historial.
- **QA:** Ejecutar casos con imágenes válidas e inválidas, distintos resultados de reconocimiento, permisos, historial y fallas.
- **Analista:** Definir el flujo de cámara y la presentación comprensible de estados, advertencias y errores.

## HU11: Conservación de imágenes con rostros desconocidos

**Descripción:** Como árbitro asignado, quiero que se conserve automáticamente la foto grupal cuando el escaneo deja rostros sin identificar, para disponer de evidencia de esos casos sin guardar imágenes de escaneos completamente reconocidos.

**Validaciones:**

- Si el escaneo termina con al menos un rostro desconocido, se conserva una foto grupal privada vinculada a ese escaneo.
- Si todos los rostros fueron identificados, se guarda el resultado del escaneo y se descarta la foto grupal.
- Una foto grupal no puede conservarse como evidencia de un escaneo sin rostros desconocidos.
- La foto debe cumplir todas las validaciones de formato, tamaño, dimensiones y calidad de la HU de escaneo.
- Una foto rechazada no genera historial ni archivo. Una falla al guardar tampoco debe dejar una foto o un resultado parcial.
- El historial permite saber cuáles rostros quedaron desconocidos, pero no entrega la foto grupal ni permite descargarla.

**Criterios de aceptación**

Dado un escaneo válido que termina con al menos un rostro desconocido, cuando se guarda el resultado, entonces se conserva una foto grupal privada asociada a ese escaneo y el historial permite conocer cuáles rostros quedaron sin identificar.

Dado un escaneo válido en el que todos los rostros fueron identificados, cuando se guarda el resultado, entonces se conserva el escaneo pero se descarta la foto grupal. No puede quedar una foto asociada a un resultado sin rostros desconocidos.

Dado que la foto incumple las reglas de formato, tamaño, dimensiones o calidad del escaneo, cuando se intenta procesar, entonces se informa el problema y no se crea ni historial ni archivo de imagen.

Dado que ocurre una falla al guardar el resultado o la foto, cuando la operación termina con error, entonces no quedan datos ni archivos parciales del intento.

Dado un usuario que consulta el historial de un escaneo con rostros desconocidos, cuando abre el resultado, entonces ve cuáles son esos rostros, pero no recibe la foto grupal ni una opción de descarga.

**Tareas:**

- **Backend:** Mantener la conservación condicionada y privada de imágenes asociadas a escaneos con rostros desconocidos.
- **Frontend:** Mostrar los casos desconocidos y el estado del escaneo dentro del flujo de resultados.
- **QA:** Comprobar conservación, descarte, privacidad y limpieza ante imágenes inválidas o fallas.
- **Analista:** Definir la política futura de revisión, acceso y retención de la evidencia.

## HU12: Validación manual de jugadores

**Descripción:** Como árbitro asignado, quiero revisar los rostros que el escaneo dejó sin identificar y decidir si corresponden a un jugador del equipo, a una persona ajena al plantel o a un caso que no puedo resolver.

**Validaciones:**

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

**Criterios de aceptación**

Dado un escaneo con rostros desconocidos, cuando el árbitro asignado abre su revisión, entonces puede ver cada rostro pendiente y elegir entre asignar un jugador, ignorarlo o marcarlo como no concluyente. Un árbitro ajeno al partido no puede confirmar decisiones.

Dado un rostro desconocido y un jugador del equipo que no está asociado a otro rostro del mismo escaneo, cuando el árbitro lo asigna y confirma, entonces se registra la decisión manual con jugador, autor y fecha, sin porcentaje de coincidencia y sin modificar el escaneo original.

Dado un jugador asignado manualmente, cuando se confirma la revisión, entonces se informa si tenía sanciones aplicables al inicio de ese partido con las mismas reglas históricas del escaneo automático. Esta evaluación no descuenta fechas.

Dado un rostro de una persona ajena al plantel, cuando el árbitro selecciona «Ignorar» y confirma, entonces el rostro permanece sin jugador y se registra esa decisión. Dado un rostro cuya identidad no puede determinar, cuando elige «No concluyente», entonces se registra una decisión distinta, también sin asignar jugador.

Dado que un jugador ya está asociado a otro rostro del mismo escaneo, por reconocimiento automático o por una decisión manual, cuando se intenta asignarlo de nuevo, entonces se rechaza la confirmación sin guardar decisiones parciales.

Dado que hay varios rostros desconocidos, cuando el árbitro confirma una decisión válida para cada uno, entonces se guardan todas juntas y la revisión queda resuelta. Si falta una decisión, se repite un rostro o alguna opción es inválida, no se guarda ninguna.

Dado que el partido todavía no está en curso, el árbitro no está asignado, la revisión ya fue resuelta o transcurrieron dos horas desde el final del partido, cuando se intenta confirmar decisiones, entonces se rechaza la operación.

Dado un escaneo resuelto manualmente, cuando se consulta de nuevo el resultado automático, entonces sus rostros, porcentajes y sanciones originales permanecen iguales y las decisiones manuales se muestran por separado.

**Tareas:**

- **Backend:** Desarrollar el registro de decisiones manuales y sus validaciones sin alterar el escaneo original.
- **Frontend:** Construir el flujo para revisar rostros, elegir decisiones y confirmar el conjunto completo.
- **QA:** Probar permisos, decisiones válidas e inválidas, duplicados, plazos, integridad e historial.
- **Analista:** Cerrar las decisiones pendientes sobre jugadores seleccionables, presencia, permisos de administración y ciclo de vida de la revisión.

## HU13: Registro y gestión de sanciones

**Descripción:** Como administrador o árbitro asignado, quiero registrar suspensiones a jugadores de un partido y gestionar su ciclo de vida para conservar el historial disciplinario y controlar futuras incorporaciones.

**Validaciones:**

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

**Criterios de aceptación**

Dado un partido en curso y un jugador que figura en el plantel histórico de uno de sus equipos, cuando un administrador o el árbitro asignado registra título, motivo y cantidad de fechas válidos, entonces se crea una sanción activa con igual cantidad de fechas asignadas y restantes, vinculada al jugador, partido y competición de origen.

Dado que el título o motivo está vacío o supera su longitud máxima, que las fechas no son un entero entre 1 y 10, o que faltan datos obligatorios, cuando se intenta guardar, entonces el sistema identifica los datos inválidos y no crea la sanción.

Dado un partido que no está en curso, un jugador ajeno a los dos planteles históricos o un árbitro no asignado, cuando se intenta crear la sanción, entonces se rechaza sin cambios.

Dado que se crea una sanción, cuando termina el partido de origen, entonces empieza su vigencia. El partido de origen no descuenta fechas; el conjunto de equipos asociados a la sanción queda fijado con los equipos actuales del jugador más su equipo histórico de ese partido.

Dado que equipos asociados a una sanción disputan partidos posteriores, cuando termina un grupo de partidos con horarios superpuestos, entonces se descuenta una sola fecha, incluso si varios partidos se superpusieron en cadena. Los partidos adyacentes cuentan por separado y los cambios posteriores de equipo o habilitación del jugador no alteran el consumo.

Dado un jugador con varias sanciones activas, cuando se cumplen partidos, entonces se descuenta primero la más antigua. La siguiente espera su turno y el grupo que completó una sanción no descuenta también la siguiente. Cuando el saldo llega a cero, la sanción pasa a cumplida.

Dado un jugador con una sanción activa, cuando se intenta agregarlo a un equipo nuevo o a un plantel de competición, entonces se rechaza la incorporación. Puede conservar o dejar sus equipos actuales. Una sanción cumplida o anulada no bloquea incorporaciones si no queda otra activa.

Dada una sanción activa, cuando un administrador la edita, o cuando la edita el árbitro creador mientras el partido de origen sigue en curso, entonces puede cambiar título y motivo. La duración solo puede cambiar si aún no se descontó ninguna fecha. Otro árbitro, una sanción cumplida o una anulada no admiten la edición.

Dada una sanción activa, incluso parcialmente consumida, cuando un administrador la anula, entonces queda anulada, conserva su saldo y descuentos previos, registra quién la anuló y detiene futuros consumos. Un árbitro no puede anularla; una sanción cumplida o ya anulada tampoco puede anularse.

Dado cualquier creación, edición, consumo o anulación, cuando se consulta la sanción, entonces se conserva su historial y autoría. No se elimina físicamente ni se reescriben planteles históricos.

**Tareas:**

- **Backend:** Mantener el alta, el consumo, los bloqueos de incorporación y la gestión del ciclo de vida de sanciones.
- **Frontend:** Ofrecer formularios y vistas para registrar, consultar y gestionar sanciones según permisos.
- **QA:** Ejecutar casos de datos inválidos, autorización, consumo, acumulación, edición, anulación e integridad.
- **Analista:** Acordar mensajes, flujo de gestión y reglas visibles para cada estado de sanción.

## HU14: Consulta del historial disciplinario

**Descripción:** Como administrador o árbitro, quiero consultar las sanciones y fechas restantes de un jugador para conocer su situación disciplinaria antes de un encuentro.

**Validaciones:**

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

**Criterios de aceptación**

Dado un administrador o árbitro autenticado, cuando consulta el historial de un jugador, entonces ve sus sanciones activas, cumplidas y anuladas de la más reciente a la más antigua. El historial sigue disponible si el jugador fue deshabilitado.

Dado que terminaron partidos que consumen fechas de sanción, cuando se consulta el historial o una sanción individual, entonces se muestran el estado y la cantidad de fechas restantes actualizados.

Dado que un jugador tiene más de 25 sanciones, cuando se consulta una página, entonces se muestran hasta 25 resultados y los totales de paginación. Una página fuera de rango devuelve una lista vacía con los totales correctos.

Dado que el usuario elige un estado o un intervalo válido de fechas de creación, cuando aplica el filtro, entonces ve solo las sanciones que cumplen esas condiciones. Un estado no admitido, fechas sin zona horaria, un rango invertido o una página no positiva se rechazan.

Dado un jugador que nunca recibió sanciones, cuando se consulta su historial, entonces se devuelve una lista vacía y la interfaz puede mostrar «Sin registros disciplinarios». Si el jugador o la sanción no existen, se informa que el recurso no fue encontrado.

Dada una sanción del historial, cuando se abre su detalle, entonces se muestran título, motivo, fechas asignadas y restantes, estado, origen, equipos asociados, personas que la crearon, modificaron o anularon y fechas correspondientes. No se muestran correos, tokens, imágenes, datos biométricos ni registros internos de cada descuento.

Dado un árbitro que consulta el historial, cuando intenta modificar o anular una sanción, entonces se aplican los permisos y límites de la HU13; la consulta por sí sola no concede permisos de gestión.

**Tareas:**

- **Backend:** Mantener la consulta, actualización de estados, filtros y paginación del historial disciplinario.
- **Frontend:** Presentar el historial y detalle con filtros, paginación y estados vacíos o de error.
- **QA:** Verificar visibilidad, filtros, orden, paginación, datos mostrados y control de permisos.
- **Analista:** Definir textos visibles y confirmar qué información disciplinaria debe presentarse al usuario.
