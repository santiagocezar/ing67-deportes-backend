from functools import wraps
from typing import Any, Callable

from flask import Response, jsonify, request
from pydantic import ValidationError


# Machine-readable codes stay stable and in English, while every message sent to
# an API client is localized here.  Route and service exceptions can therefore
# keep their implementation detail out of the public response.
_ERROR_MESSAGES: dict[str, str] = {
    "authentication_required": "Se requiere autenticación.",
    "invalid_token": "El token de autenticación no es válido.",
    "token_expired": "El token de autenticación venció.",
    "session_revoked": "La sesión ya no está activa.",
    "refresh_token_reused": "El token de actualización ya fue utilizado. Inicie sesión nuevamente.",
    "invalid_credentials": "Las credenciales ingresadas no son válidas.",
    "authentication_unavailable": "La autenticación no está disponible temporalmente.",
    "administrator_required": "Se requieren permisos de administrador.",
    "application_role_required": "Se requiere un rol de aplicación válido.",
    "forbidden": "No tiene permisos para realizar esta acción.",
    "invalid_request": "La solicitud no tiene el formato requerido.",
    "request_too_large": "El cuerpo de la solicitud es demasiado grande.",
    "validation_error": "La solicitud contiene datos no válidos.",
    "immutable_field": "El campo indicado no puede modificarse.",
    "resource_not_found": "El recurso solicitado no existe.",
    "method_not_allowed": "El método HTTP no está permitido para este recurso.",
    "request_error": "No se pudo completar la solicitud.",
    "internal_error": "Ocurrió un error inesperado al procesar la solicitud.",
    "service_unavailable": "El servicio no está disponible temporalmente.",
    "sport_not_found": "El deporte solicitado no existe.",
    "sport_name_conflict": "Ya existe un deporte con ese nombre.",
    "sport_in_use": "No se puede modificar el deporte porque está en uso.",
    "team_not_found": "El equipo solicitado no existe.",
    "team_disabled": "El equipo está deshabilitado.",
    "team_name_conflict": "Ya existe un equipo con ese nombre para el deporte y categoría seleccionados.",
    "team_sport_mismatch": "El equipo no corresponde al deporte seleccionado.",
    "team_gender_mismatch": "El equipo no corresponde a la categoría de género seleccionada.",
    "team_capacity_reached": "El equipo ya alcanzó la cantidad máxima de jugadores.",
    "player_not_found": "El jugador solicitado no existe.",
    "player_disabled": "El jugador está deshabilitado.",
    "player_active_sanction": "El jugador tiene una sanción activa.",
    "player_not_in_team": "El jugador no pertenece al equipo seleccionado.",
    "player_competition_mismatch": "El jugador no cumple los requisitos de la competencia.",
    "email_conflict": "Ya existe un usuario con ese correo electrónico.",
    "user_not_found": "El usuario solicitado ya no existe.",
    "photo_too_large": "La foto no puede superar los 5 MiB.",
    "unsupported_photo_format": "Solo se permiten fotos PNG o JPG.",
    "invalid_photo_dimensions": "Las dimensiones de la foto no son válidas.",
    "invalid_face_count": "La foto debe contener exactamente un rostro visible.",
    "face_too_small": "El rostro detectado es demasiado pequeño.",
    "invalid_photo": "No se pudo validar la foto.",
    "photo_identity_mismatch": "La foto no coincide con la identidad del jugador.",
    "player_photo_limit_reached": "El jugador ya alcanzó el máximo de tres fotos.",
    "initial_reference_photo_required": "No se puede eliminar la foto de referencia mientras existan otras fotos.",
    "photo_not_found": "La foto solicitada no existe.",
    "photo_storage_unavailable": "El almacenamiento de fotos no está disponible temporalmente.",
    "photo_processing_unavailable": "La validación de fotos no está disponible temporalmente.",
    "photo_identity_validation_unavailable": "La validación de identidad de la foto no está disponible temporalmente.",
    "competition_not_found": "La competencia solicitada no existe.",
    "competition_disabled": "La competencia está deshabilitada.",
    "competition_ended": "La competencia ya finalizó.",
    "competition_finished": "La competencia ya finalizó.",
    "competition_end_not_future": "La fecha de finalización de la competencia debe ser futura.",
    "competition_date_range_invalid": "La fecha de finalización de la competencia debe ser posterior a la fecha de inicio.",
    "competition_has_matches": "No se puede realizar esta operación porque la competencia ya tiene partidos.",
    "competition_team_count_immutable": "No se puede modificar la cantidad de equipos de una competencia existente.",
    "competition_roster_size_invalid": "La lista de jugadores no cumple la cantidad permitida para el deporte.",
    "competition_player_already_registered": "Un jugador solo puede representar a un equipo en la competencia.",
    "competition_participants_invalid": "Los participantes de la competencia no son válidos.",
    "import_same_competition": "La competencia de origen debe ser distinta de la competencia de destino.",
    "import_source_not_finished": "La competencia de origen debe haber finalizado.",
    "import_competition_mismatch": "Las competencias deben tener el mismo deporte y categoría de género.",
    "competition_draw_locked": "El sorteo no puede modificarse porque hay partidos en curso o finalizados.",
    "referee_not_found": "El árbitro seleccionado no existe.",
    "invalid_referee_role": "El usuario seleccionado no tiene rol de árbitro.",
    "match_not_found": "El partido solicitado no existe.",
    "match_already_started": "El partido ya comenzó.",
    "match_date_range_invalid": "La fecha de finalización del partido debe ser posterior a la fecha de inicio.",
    "match_retroactive_schedule": "No se puede programar un partido en el pasado.",
    "match_outside_competition": "El horario del partido debe estar dentro de la competencia.",
    "match_not_scheduled": "El partido no está programado.",
    "match_not_in_progress": "El partido no está en curso.",
    "player_not_in_match_roster": "El jugador no pertenece a la lista histórica del partido.",
    "match_referee_required": "Solo el árbitro asignado al partido puede realizar esta acción.",
    "sanction_not_found": "La sanción solicitada no existe.",
    "sanction_owner_required": "Solo quien creó la sanción puede realizar esta acción.",
    "sanction_edit_window_closed": "El plazo para editar la sanción ya finalizó.",
    "sanction_not_active": "La sanción no está activa.",
    "sanction_duration_locked": "No se puede modificar la duración después del primer consumo.",
    "sanction_already_served": "La sanción ya fue cumplida.",
    "sanction_already_annulled": "La sanción ya fue anulada.",
    "scan_forbidden": "No tiene permisos para consultar o crear este escaneo.",
    "scan_window_closed": "El período permitido para realizar el escaneo ya finalizó.",
    "scan_team_not_found": "El equipo seleccionado no existe.",
    "scan_team_not_in_match": "El equipo seleccionado no participa en el partido.",
    "roster_scan_not_found": "El escaneo solicitado no existe.",
    "group_image_not_available": "La imagen grupal no está disponible.",
    "scan_image_not_retained": "La imagen grupal no fue conservada para revisión manual.",
    "invalid_scan_image": "Los datos de la imagen grupal no son válidos.",
    "invalid_scan_sanction": "La sanción asociada al escaneo no es válida.",
    "duplicate_scan_sanction": "No se puede asociar la misma sanción más de una vez al rostro.",
    "invalid_scan_face_status": "El estado del rostro detectado no es válido.",
    "invalid_scan_face_box": "Las coordenadas del rostro detectado no son válidas.",
    "invalid_unknown_scan_face": "El rostro desconocido no tiene datos válidos.",
    "invalid_recognized_scan_face": "El rostro reconocido no tiene datos válidos.",
    "invalid_scan_face_sanctions": "Las sanciones asociadas al rostro no son válidas.",
    "duplicate_scan_face_index": "No se puede registrar el mismo rostro más de una vez.",
    "scan_image_too_large": "La imagen grupal no puede superar los 10 MiB.",
    "unsupported_image_format": "Solo se permiten imágenes grupales PNG o JPEG.",
    "image_not_processable": "No se pudo procesar la imagen grupal.",
    "scan_processing_unavailable": "El procesamiento del escaneo no está disponible temporalmente.",
    "manual_review_unavailable": "La revisión manual no está disponible temporalmente.",
    "manual_review_forbidden": "No tiene permisos para consultar esta revisión manual.",
    "manual_review_referee_required": "Solo el árbitro asignado al partido puede resolver la revisión manual.",
    "manual_review_not_found": "La revisión manual solicitada no existe.",
    "manual_review_closed": "La revisión manual ya no está disponible.",
    "manual_review_already_finalized": "La revisión manual ya fue finalizada.",
    "manual_review_decisions_required": "Debe informar al menos una decisión de revisión manual.",
    "manual_review_confirmation_required": "Debe confirmar la conservación para revisión manual.",
    "manual_review_decisions_incomplete": "Debe resolver todos los rostros desconocidos de la revisión manual.",
    "invalid_manual_review_face_index": "Uno de los rostros indicados no pertenece a esta revisión manual.",
    "duplicate_manual_review_face": "No se puede decidir más de una vez sobre el mismo rostro.",
    "invalid_manual_review_outcome": "El resultado de la revisión manual no es válido.",
    "manual_review_player_required": "Debe indicar un jugador cuando el rostro pertenece al equipo.",
    "manual_review_player_forbidden": "Solo puede indicar un jugador cuando el rostro pertenece al equipo.",
    "duplicate_manual_review_player": "No se puede asignar el mismo jugador a más de un rostro.",
    "manual_review_player_not_in_roster": "El jugador indicado no pertenece a la lista histórica del equipo.",
    "manual_review_evidence_unavailable": "La evidencia privada de la revisión manual no está disponible.",
    "invalid_manual_review_evidence": "La evidencia privada de la revisión manual no es válida.",
    "invalid_manual_review_status": "El estado de revisión manual indicado no es válido.",
    "invalid_manual_review_match_id": "El partido indicado para la revisión manual no es válido.",
    "invalid_manual_review_sort": "El orden indicado para la revisión manual no es válido.",
    "invalid_manual_review_page": "La página solicitada no es válida.",
}

_VALIDATION_MESSAGES: dict[str, str] = {
    "missing": "Este campo es obligatorio.",
    "extra_forbidden": "No se permiten campos adicionales.",
    "json_invalid": "El cuerpo JSON no tiene un formato válido.",
    "string_type": "Debe ser un texto.",
    "string_too_short": "La longitud del texto es demasiado corta.",
    "string_too_long": "La longitud del texto supera el máximo permitido.",
    "int_type": "Debe ser un número entero.",
    "int_parsing": "Debe ser un número entero válido.",
    "int_from_float": "Debe ser un número entero válido.",
    "bool_type": "Debe ser verdadero o falso.",
    "bool_parsing": "Debe ser verdadero o falso.",
    "list_type": "Debe ser una lista.",
    "list_too_short": "La lista tiene menos elementos de los permitidos.",
    "list_too_long": "La lista supera la cantidad máxima de elementos.",
    "too_short": "La cantidad de elementos es menor que la permitida.",
    "too_long": "La cantidad de elementos supera la permitida.",
    "greater_than": "Debe ser mayor que el límite permitido.",
    "greater_than_equal": "Debe ser mayor o igual que el límite permitido.",
    "less_than": "Debe ser menor que el límite permitido.",
    "less_than_equal": "Debe ser menor o igual que el límite permitido.",
    "literal_error": "Debe coincidir con una de las opciones permitidas.",
    "enum": "Debe coincidir con una de las opciones permitidas.",
    "date_type": "Debe ser una fecha válida.",
    "date_parsing": "Debe ser una fecha válida.",
    "date_from_datetime_parsing": "Debe ser una fecha válida.",
    "datetime_type": "Debe ser una fecha y hora válidas.",
    "datetime_parsing": "Debe ser una fecha y hora válidas.",
    "datetime_from_date_parsing": "Debe ser una fecha y hora válidas.",
    "timezone_aware_datetime_required": "Debe incluir una zona horaria.",
    "competition_date_range_invalid": "La fecha de finalización debe ser posterior a la fecha de inicio.",
    "match_date_range_invalid": "La fecha de finalización debe ser posterior a la fecha de inicio.",
    "sanction_created_date_range_invalid": "La fecha final debe ser posterior o igual a la fecha inicial.",
    "immutable_field": "Este campo no puede modificarse.",
    "duplicate_team_ids": "No se permiten equipos repetidos.",
    "sanction_text_blank": "Este campo es obligatorio.",
    "photo_file_required": "Debe adjuntar un archivo de foto válido.",
    "manual_review_confirmation_required": "Debe confirmar la conservación para revisión manual.",
    "manual_review_player_required": "Debe indicar un jugador para este resultado.",
    "manual_review_player_forbidden": "No debe indicar un jugador para este resultado.",
    "multiple_files": "Debe adjuntar exactamente un archivo.",
    "multiple_values": "Debe informar un único valor.",
    "image_too_large": "La imagen supera el tamaño máximo permitido.",
    "unsupported_image_format": "El formato de imagen no está permitido.",
    "invalid_image": "La imagen no es válida.",
    "no_detectable_faces": "No se detectaron rostros en la imagen.",
    "image_quality_insufficient": "La calidad de la imagen no es suficiente.",
}

_DEFAULT_ERROR_MESSAGE = "No se pudo procesar la solicitud."
_DEFAULT_VALIDATION_MESSAGE = "El valor ingresado no es válido."


def localized_error_message(code: str) -> str:
    """Return a safe Spanish message for a stable public error code."""

    return _ERROR_MESSAGES.get(code, _DEFAULT_ERROR_MESSAGE)


def localized_validation_message(error_type: str) -> str:
    """Return a Spanish message for a Pydantic or form validation type."""

    return _VALIDATION_MESSAGES.get(error_type, _DEFAULT_VALIDATION_MESSAGE)


def _localized_details(
    details: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Keep stable detail fields/types while localizing their messages."""

    localized: list[dict[str, str]] = []
    for detail in details:
        error_type = str(detail.get("type", "value_error"))
        localized.append(
            {
                "field": str(detail.get("field", "body")),
                "message": localized_validation_message(error_type),
                "type": error_type,
            }
        )
    return localized


def error_response(
    code: str,
    _message: str,
    status: int,
    *,
    details: list[dict[str, str]] | None = None,
) -> tuple[Response, int]:
    """Build the common API error response with Spanish user messages."""

    error: dict[str, Any] = {
        "code": code,
        "message": localized_error_message(code),
    }
    if details:
        error["details"] = _localized_details(details)
    return jsonify(error=error), status


def _validation_source(location: tuple[Any, ...]) -> str:
    first_part = str(location[0]) if location else ""
    if first_part and first_part in (request.view_args or {}):
        return "path"
    if first_part and first_part in request.args:
        return "query"
    return "body"


def validation_details(error: ValidationError) -> list[dict[str, str]]:
    """Return stable validation details without Pydantic input values."""
    details: list[dict[str, str]] = []
    for item in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = tuple(item.get("loc", ()))
        source = _validation_source(location)
        suffix = ".".join(str(part) for part in location)
        field = f"{source}.{suffix}" if suffix else source
        details.append(
            {
                "field": field,
                "message": localized_validation_message(
                    str(item.get("type", "value_error"))
                ),
                "type": str(item.get("type", "value_error")),
            }
        )
    return details


def validation_error_response(error: ValidationError) -> Response:
    """Translate flask-openapi3/Pydantic failures into the API error envelope."""
    if request.endpoint in {"auth.signup", "auth.login"}:
        invalid_body = _json_object_error()
        if invalid_body is not None:
            response, status = invalid_body
            response.status_code = status
            return response

    details = validation_details(error)
    immutable_error = next(
        (detail for detail in details if detail["type"] == "immutable_field"),
        None,
    )
    domain_error = next(
        (
            detail
            for detail in details
            if detail["type"]
            in {
                "competition_date_range_invalid",
                "match_date_range_invalid",
            }
        ),
        None,
    )
    if domain_error:
        response, status = error_response(
            domain_error["type"],
            domain_error["message"],
            422,
            details=details,
        )
    elif immutable_error:
        response, status = error_response(
            "immutable_field",
            immutable_error["message"],
            422,
            details=details,
        )
    else:
        response, status = error_response(
            "validation_error",
            "Request validation failed.",
            422,
            details=details,
        )
    response.status_code = status
    return response


def _json_object_error() -> tuple[Response, int] | None:
    if not request.is_json:
        return error_response(
            "invalid_request",
            "A JSON request body is required.",
            400,
        )

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error_response(
            "invalid_request",
            "A JSON request body is required.",
            400,
        )
    return None


def json_object_required(function: Callable) -> Callable:
    """Validate a protected JSON body after its authorization decorator."""

    @wraps(function)
    def wrapper(*args, **kwargs):
        invalid_body = _json_object_error()
        if invalid_body is not None:
            return invalid_body
        return function(*args, **kwargs)

    return wrapper


def multipart_form_required(function: Callable) -> Callable:
    """Validate a protected multipart body after its authorization decorator."""

    @wraps(function)
    def wrapper(*args, **kwargs):
        if request.mimetype != "multipart/form-data":
            return error_response(
                "invalid_request",
                "A multipart/form-data request body is required.",
                400,
            )
        return function(*args, **kwargs)

    return wrapper
