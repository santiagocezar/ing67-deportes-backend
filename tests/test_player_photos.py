import importlib
import io
import shutil
import struct
import tempfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from flask_jwt_extended import create_access_token
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import configure_mappers
from werkzeug.datastructures import FileStorage
from werkzeug.test import encode_multipart

from app.app import create_app
from app.extensions import db
from app.models import Player, PlayerPhoto
from app.schemas.player_photos import (
    PlayerPhotoListResponse,
    PlayerPhotoResponse,
)
from app.services.player_photos import (
    JPEG_FORMAT,
    MAX_PLAYER_PHOTO_BYTES,
    PNG_FORMAT,
    InvalidPhotoError,
    PhotoTooLargeError,
    PlayerPhotoNotFoundError,
    StoredPhotoFile,
    UnsupportedPhotoFormatError,
    _store_photo_file,
    add_player_photo,
    get_player_photo_file,
    list_player_photos,
    validate_photo,
)
from app.services.players import PlayerDisabledError, PlayerNotFoundError


TEST_CONFIG = {
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": "postgresql://test:test@localhost/test",
    "JWT_SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
    "API_DOCS_ENABLED": True,
}
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
SVG_BYTES = b"<svg xmlns='http://www.w3.org/2000/svg' width='8' height='8'/>"


def _synthetic_image(extension: str) -> bytes:
    """Encode a generated image; tests never use real Player photos."""
    pixels = np.zeros((8, 8, 3), dtype=np.uint8)
    pixels[2:6, 2:6] = (40, 120, 200)
    encoded, buffer = cv2.imencode(extension, pixels)
    if not encoded:
        raise RuntimeError(f"Could not encode a {extension} test image.")
    return buffer.tobytes()


PNG_BYTES = _synthetic_image(".png")
JPEG_BYTES = _synthetic_image(".jpg")
BMP_BYTES = _synthetic_image(".bmp")


def _png_of_size(size: int) -> bytes:
    """Return a valid PNG padded with a tEXt chunk to exactly size bytes."""
    keyword = b"Comment\0"
    text = keyword + b"x" * (size - len(PNG_BYTES) - 12 - len(keyword))
    chunk = (
        struct.pack(">I", len(text))
        + b"tEXt"
        + text
        + struct.pack(">I", zlib.crc32(b"tEXt" + text))
    )
    iend_start = PNG_BYTES.rindex(b"IEND") - 4
    return PNG_BYTES[:iend_start] + chunk + PNG_BYTES[iend_start:]


def _photo(photo_id=1, *, player_id=1, content_type="image/png"):
    return SimpleNamespace(
        id=photo_id,
        player_id=player_id,
        content_type=content_type,
        created_at=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
        file_name="must-not-leak.png",
    )


class PlayerPhotoValidationTests(unittest.TestCase):
    def test_png_and_jpg_are_accepted_with_any_extension_case(self):
        cases = (
            ("photo.png", PNG_BYTES, PNG_FORMAT),
            ("PHOTO.PNG", PNG_BYTES, PNG_FORMAT),
            ("photo.jpg", JPEG_BYTES, JPEG_FORMAT),
            ("photo.jpeg", JPEG_BYTES, JPEG_FORMAT),
            ("Photo.JPEG", JPEG_BYTES, JPEG_FORMAT),
        )
        for file_name, content, expected_format in cases:
            with self.subTest(file_name=file_name):
                self.assertEqual(
                    validate_photo(file_name, content),
                    expected_format,
                )

    def test_disallowed_extensions_are_rejected(self):
        cases = (
            ("report.pdf", PDF_BYTES),
            ("logo.svg", SVG_BYTES),
            ("photo.gif", PNG_BYTES),
            ("photo.bmp", BMP_BYTES),
            ("photo.png.pdf", PNG_BYTES),
            ("photo", PNG_BYTES),
        )
        for file_name, content in cases:
            with self.subTest(file_name=file_name):
                with self.assertRaises(UnsupportedPhotoFormatError):
                    validate_photo(file_name, content)

    def test_content_that_is_not_png_or_jpg_is_rejected_despite_extension(self):
        cases = (
            ("report.jpg", PDF_BYTES),
            ("logo.png", SVG_BYTES),
            ("bitmap.png", BMP_BYTES),
        )
        for file_name, content in cases:
            with self.subTest(file_name=file_name):
                with self.assertRaises(UnsupportedPhotoFormatError):
                    validate_photo(file_name, content)

    def test_stored_format_comes_from_content(self):
        self.assertEqual(validate_photo("renamed.jpg", PNG_BYTES), PNG_FORMAT)

    def test_five_megabyte_limit_is_inclusive(self):
        self.assertEqual(
            validate_photo("limit.png", _png_of_size(MAX_PLAYER_PHOTO_BYTES)),
            PNG_FORMAT,
        )
        oversized = _png_of_size(MAX_PLAYER_PHOTO_BYTES + 1)
        with self.assertRaises(PhotoTooLargeError):
            validate_photo("oversized.png", oversized)

    def test_empty_and_undecodable_images_are_rejected(self):
        cases = (
            ("empty.png", b""),
            ("corrupt.png", PNG_FORMAT.signature + b"not an image" * 4),
            ("truncated.png", PNG_BYTES[: len(PNG_BYTES) // 2]),
            ("truncated.jpg", JPEG_BYTES[: len(JPEG_BYTES) // 2]),
        )
        for file_name, content in cases:
            with self.subTest(file_name=file_name):
                with self.assertRaises(InvalidPhotoError):
                    validate_photo(file_name, content)


class PlayerPhotoStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.photos_dir = self.temp_dir / "player_photos"
        self.app = create_app(
            {**TEST_CONFIG, "PLAYER_PHOTOS_DIR": str(self.photos_dir)}
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _stored_files(self) -> list[Path]:
        if not self.photos_dir.exists():
            return []
        return list(self.photos_dir.iterdir())

    @staticmethod
    def _player_lookup(player):
        result = MagicMock()
        result.scalar_one_or_none.return_value = player
        return result

    def test_valid_photo_is_stored_under_a_uuid_name_and_committed(self):
        player = SimpleNamespace(id=7, is_enabled=True)
        with (
            self.app.app_context(),
            patch.object(
                db.session,
                "execute",
                return_value=self._player_lookup(player),
            ) as execute,
            patch.object(db.session, "add") as add,
            patch.object(db.session, "commit") as commit,
        ):
            photo = add_player_photo(
                7,
                file_name="player photo.JPEG",
                content=JPEG_BYTES,
            )

        self.assertIsInstance(photo, PlayerPhoto)
        self.assertEqual(photo.player_id, 7)
        self.assertEqual(photo.content_type, "image/jpeg")
        self.assertRegex(photo.file_name, r"^[0-9a-f]{32}\.jpg$")
        stored_files = self._stored_files()
        self.assertEqual([path.name for path in stored_files], [photo.file_name])
        self.assertEqual(stored_files[0].read_bytes(), JPEG_BYTES)
        add.assert_called_once_with(photo)
        commit.assert_called_once()

        statement = execute.call_args.args[0]
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FOR SHARE", sql)

    def test_rejected_photo_never_reaches_the_database_or_disk(self):
        with (
            self.app.app_context(),
            patch.object(db.session, "execute") as execute,
        ):
            with self.assertRaises(UnsupportedPhotoFormatError):
                add_player_photo(1, file_name="report.pdf", content=PDF_BYTES)

        execute.assert_not_called()
        self.assertEqual(self._stored_files(), [])

    def test_missing_or_disabled_player_writes_no_file(self):
        cases = (
            (None, PlayerNotFoundError),
            (SimpleNamespace(id=1, is_enabled=False), PlayerDisabledError),
        )
        for player, expected_error in cases:
            with (
                self.subTest(expected_error=expected_error),
                self.app.app_context(),
                patch.object(
                    db.session,
                    "execute",
                    return_value=self._player_lookup(player),
                ),
                patch.object(db.session, "rollback") as rollback,
            ):
                with self.assertRaises(expected_error):
                    add_player_photo(1, file_name="photo.png", content=PNG_BYTES)
                rollback.assert_called_once()
                self.assertEqual(self._stored_files(), [])

    def test_database_failure_removes_the_stored_file(self):
        player = SimpleNamespace(id=1, is_enabled=True)
        with (
            self.app.app_context(),
            patch.object(
                db.session,
                "execute",
                return_value=self._player_lookup(player),
            ),
            patch.object(db.session, "add"),
            patch.object(
                db.session,
                "commit",
                side_effect=SQLAlchemyError("failure"),
            ),
            patch.object(db.session, "rollback") as rollback,
        ):
            with self.assertRaises(SQLAlchemyError):
                add_player_photo(1, file_name="photo.png", content=PNG_BYTES)

        rollback.assert_called_once()
        self.assertEqual(self._stored_files(), [])

    def test_storage_failure_rolls_back_without_persisting(self):
        self.photos_dir.write_text("a file where the folder should be")
        player = SimpleNamespace(id=1, is_enabled=True)
        with (
            self.app.app_context(),
            patch.object(
                db.session,
                "execute",
                return_value=self._player_lookup(player),
            ),
            patch.object(db.session, "add") as add,
            patch.object(db.session, "rollback") as rollback,
        ):
            with self.assertRaises(OSError):
                add_player_photo(1, file_name="photo.png", content=PNG_BYTES)

        add.assert_not_called()
        rollback.assert_called_once()

    def test_partial_file_is_removed_when_writing_fails(self):
        with self.app.app_context():
            with self.assertRaises(TypeError):
                _store_photo_file("not bytes", ".png")

        self.assertEqual(self._stored_files(), [])

    def test_listing_requires_an_existing_player(self):
        photos = [_photo(1), _photo(2)]
        with (
            self.app.app_context(),
            patch.object(
                db.session,
                "get",
                return_value=SimpleNamespace(photos=photos),
            ),
        ):
            self.assertEqual(list_player_photos(1), photos)

        with (
            self.app.app_context(),
            patch.object(db.session, "get", return_value=None),
        ):
            with self.assertRaises(PlayerNotFoundError):
                list_player_photos(999)

    def test_image_lookup_checks_owner_and_stored_file(self):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        with (
            self.app.app_context(),
            patch.object(db.session, "execute", return_value=result) as execute,
        ):
            with self.assertRaises(PlayerPhotoNotFoundError):
                get_player_photo_file(1, 5)

        sql = str(
            execute.call_args.args[0].compile(dialect=postgresql.dialect())
        )
        self.assertIn("player_photos.id =", sql)
        self.assertIn("player_photos.player_id =", sql)

        photo = SimpleNamespace(
            id=5,
            file_name=f"{'a' * 32}.png",
            content_type="image/png",
        )
        result.scalar_one_or_none.return_value = photo
        with (
            self.app.app_context(),
            patch.object(db.session, "execute", return_value=result),
            self.assertLogs(self.app.logger, level="ERROR"),
        ):
            with self.assertRaises(PlayerPhotoNotFoundError):
                get_player_photo_file(1, 5)

        self.photos_dir.mkdir()
        stored_path = self.photos_dir / photo.file_name
        stored_path.write_bytes(PNG_BYTES)
        with (
            self.app.app_context(),
            patch.object(db.session, "execute", return_value=result),
        ):
            self.assertEqual(
                get_player_photo_file(1, 5),
                StoredPhotoFile(path=stored_path, content_type="image/png"),
            )


class PlayerPhotoApiTests(unittest.TestCase):
    def setUp(self):
        self.blocklist_patch = patch(
            "app.services.auth.is_token_revoked",
            return_value=False,
        )
        self.blocklist_patch.start()
        self.temp_dir = Path(tempfile.mkdtemp())
        self.app = create_app(
            {**TEST_CONFIG, "PLAYER_PHOTOS_DIR": str(self.temp_dir)}
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            self.admin_token = create_access_token(
                identity="1",
                additional_claims={
                    "sid": "admin-session",
                    "role": "administrator",
                },
            )
            self.referee_token = create_access_token(
                identity="2",
                additional_claims={
                    "sid": "referee-session",
                    "role": "referee",
                },
            )

    def tearDown(self):
        self.blocklist_patch.stop()
        shutil.rmtree(self.temp_dir)

    @staticmethod
    def _bearer(token):
        return {"Authorization": f"Bearer {token}"}

    def _upload(self, fields=None, *, url="/players/1/photos"):
        """Post (content, file name) tuples as files and strings as fields."""
        if fields is None:
            fields = {"photo": (PNG_BYTES, "photo.png")}
        # Encode in memory: the test client spools large bodies to temporary
        # files that it never closes.
        boundary, body = encode_multipart(
            {
                name: (
                    FileStorage(io.BytesIO(value[0]), filename=value[1])
                    if isinstance(value, tuple)
                    else value
                )
                for name, value in fields.items()
            }
        )
        return self.client.post(
            url,
            headers=self._bearer(self.admin_token),
            data=body,
            content_type=f'multipart/form-data; boundary="{boundary}"',
        )

    def test_administrator_can_upload_list_and_view_photos(self):
        with patch(
            "app.routes.player_photos.add_player_photo",
            return_value=_photo(),
        ) as service:
            response = self._upload()
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        PlayerPhotoResponse.model_validate(payload)
        self.assertEqual(
            set(payload),
            {"id", "player_id", "content_type", "created_at"},
        )
        service.assert_called_once_with(
            1,
            file_name="photo.png",
            content=PNG_BYTES,
        )

        photos = [_photo(1), _photo(2, content_type="image/jpeg")]
        with patch(
            "app.routes.player_photos.list_player_photos",
            return_value=photos,
        ):
            response = self.client.get(
                "/players/1/photos",
                headers=self._bearer(self.admin_token),
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        PlayerPhotoListResponse.model_validate(payload)
        self.assertEqual([photo["id"] for photo in payload["photos"]], [1, 2])
        self.assertNotIn("must-not-leak", response.get_data(as_text=True))

        stored_path = self.temp_dir / "stored.png"
        stored_path.write_bytes(PNG_BYTES)
        with patch(
            "app.routes.player_photos.get_player_photo_file",
            return_value=StoredPhotoFile(stored_path, "image/png"),
        ) as service:
            with self.client.get(
                "/players/1/photos/2",
                headers=self._bearer(self.admin_token),
            ) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "image/png")
                self.assertEqual(response.get_data(), PNG_BYTES)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(
                    response.headers["X-Content-Type-Options"],
                    "nosniff",
                )
        service.assert_called_once_with(1, 2)

    def test_every_photo_operation_requires_an_administrator_token(self):
        requests = (
            ("post", "/players/1/photos"),
            ("get", "/players/1/photos"),
            ("get", "/players/1/photos/1"),
        )
        with (
            patch("app.routes.player_photos.add_player_photo") as upload,
            patch("app.routes.player_photos.list_player_photos") as listing,
            patch("app.routes.player_photos.get_player_photo_file") as image,
        ):
            for method, url in requests:
                with self.subTest(method=method, url=url, role="anonymous"):
                    response = self.client.open(url, method=method)
                    self.assertEqual(response.status_code, 401)
                with self.subTest(method=method, url=url, role="referee"):
                    response = self.client.open(
                        url,
                        method=method,
                        headers=self._bearer(self.referee_token),
                    )
                    self.assertEqual(response.status_code, 403)

        upload.assert_not_called()
        listing.assert_not_called()
        image.assert_not_called()

    def test_upload_without_a_multipart_body_returns_400(self):
        headers = self._bearer(self.admin_token)
        requests = (
            self.client.post("/players/1/photos", headers=headers),
            self.client.post(
                "/players/1/photos",
                headers=headers,
                json={"photo": "photo.png"},
            ),
            self.client.post(
                "/players/1/photos",
                headers=headers,
                data={"photo": "photo.png"},
            ),
        )
        for response in requests:
            with self.subTest(response=response):
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.get_json()["error"]["code"],
                    "invalid_request",
                )

    def test_upload_form_contract_errors_return_422(self):
        invalid_forms = (
            {"note": "no file"},
            {"photo": "not-a-file"},
            {"photo": (b"", "")},
            {"photo": (PNG_BYTES, "photo.png"), "player_id": "2"},
        )
        with patch("app.routes.player_photos.add_player_photo") as service:
            for data in invalid_forms:
                with self.subTest(fields=sorted(data)):
                    response = self._upload(data)
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(
                        response.get_json()["error"]["code"],
                        "validation_error",
                    )

            response = self._upload(url="/players/0/photos")
            self.assertEqual(response.status_code, 422)
        service.assert_not_called()

    def test_oversized_request_is_rejected_before_it_is_parsed(self):
        oversized = b"\0" * (MAX_PLAYER_PHOTO_BYTES + 65 * 1024)
        with patch("app.routes.player_photos.add_player_photo") as service:
            response = self._upload({"photo": (oversized, "huge.png")})

        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            response.get_json()["error"],
            {
                "code": "photo_too_large",
                "message": "Each photo must be at most 5 MB.",
            },
        )
        service.assert_not_called()

    def test_rejected_files_explain_the_reason_and_are_not_stored(self):
        cases = (
            ("report.pdf", PDF_BYTES, 415, "unsupported_photo_format"),
            ("logo.svg", SVG_BYTES, 415, "unsupported_photo_format"),
            ("renamed.jpg", PDF_BYTES, 415, "unsupported_photo_format"),
            (
                "oversized.png",
                _png_of_size(MAX_PLAYER_PHOTO_BYTES + 1),
                413,
                "photo_too_large",
            ),
            ("corrupt.png", PNG_BYTES[:40], 422, "invalid_photo"),
        )
        with patch("app.services.player_photos.db") as database:
            for file_name, content, status, code in cases:
                with self.subTest(file_name=file_name):
                    response = self._upload({"photo": (content, file_name)})
                    self.assertEqual(response.status_code, status)
                    error = response.get_json()["error"]
                    self.assertEqual(error["code"], code)
                    self.assertTrue(error["message"])

        database.session.execute.assert_not_called()
        database.session.commit.assert_not_called()
        self.assertEqual(list(self.temp_dir.iterdir()), [])

    def test_upload_maps_domain_and_infrastructure_errors(self):
        cases = (
            (UnsupportedPhotoFormatError(), 415, "unsupported_photo_format"),
            (PhotoTooLargeError(), 413, "photo_too_large"),
            (InvalidPhotoError(), 422, "invalid_photo"),
            (PlayerNotFoundError("missing"), 404, "player_not_found"),
            (PlayerDisabledError("disabled"), 409, "player_disabled"),
            (
                SQLAlchemyError("private database detail"),
                503,
                "service_unavailable",
            ),
            (
                OSError("private storage path"),
                503,
                "photo_storage_unavailable",
            ),
        )
        for exception, status, code in cases:
            with (
                self.subTest(code=code),
                patch(
                    "app.routes.player_photos.add_player_photo",
                    side_effect=exception,
                ),
            ):
                response = self._upload()
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.get_json()["error"]["code"], code)
                self.assertNotIn("private", response.get_data(as_text=True))

    def test_list_and_image_map_errors(self):
        headers = self._bearer(self.admin_token)
        list_service = "app.routes.player_photos.list_player_photos"
        image_service = "app.routes.player_photos.get_player_photo_file"
        cases = (
            (
                "/players/1/photos",
                list_service,
                PlayerNotFoundError("missing"),
                404,
                "player_not_found",
            ),
            (
                "/players/1/photos",
                list_service,
                SQLAlchemyError("failure"),
                503,
                "service_unavailable",
            ),
            (
                "/players/1/photos/9",
                image_service,
                PlayerPhotoNotFoundError("missing"),
                404,
                "photo_not_found",
            ),
            (
                "/players/1/photos/9",
                image_service,
                SQLAlchemyError("failure"),
                503,
                "service_unavailable",
            ),
        )
        for url, target, exception, status, code in cases:
            with (
                self.subTest(url=url, code=code),
                patch(target, side_effect=exception),
            ):
                response = self.client.get(url, headers=headers)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.get_json()["error"]["code"], code)

    def test_openapi_documents_multipart_upload_and_image_download(self):
        contract = self.client.get("/openapi.json").get_json()
        collection = contract["paths"]["/players/{player_id}/photos"]
        item = contract["paths"]["/players/{player_id}/photos/{photo_id}"]

        self.assertEqual(
            (
                collection["post"]["operationId"],
                collection["get"]["operationId"],
                item["get"]["operationId"],
            ),
            ("playerPhotosUpload", "playerPhotosList", "playerPhotosGetImage"),
        )
        self.assertEqual(
            set(collection["post"]["requestBody"]["content"]),
            {"multipart/form-data"},
        )
        self.assertTrue(
            {"400", "404", "409", "413", "415", "503"}.issubset(
                collection["post"]["responses"]
            )
        )
        self.assertEqual(
            set(item["get"]["responses"]["200"]["content"]),
            {"image/png", "image/jpeg"},
        )

        schemas = contract["components"]["schemas"]
        upload_form = schemas["PlayerPhotoUploadForm"]
        self.assertEqual(upload_form["required"], ["photo"])
        self.assertEqual(upload_form["properties"]["photo"]["format"], "binary")
        self.assertFalse(upload_form["additionalProperties"])
        self.assertEqual(
            set(schemas["PlayerPhotoResponse"]["properties"]),
            {"id", "player_id", "content_type", "created_at"},
        )


class PlayerPhotoModelTests(unittest.TestCase):
    def test_model_stores_only_a_file_reference_and_metadata(self):
        table = PlayerPhoto.__table__
        self.assertEqual(
            set(table.columns.keys()),
            {"id", "player_id", "file_name", "content_type", "created_at"},
        )
        constraint_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, (CheckConstraint, UniqueConstraint))
        }
        self.assertEqual(
            constraint_names,
            {"ck_player_photos_content_type", "uq_player_photos_file_name"},
        )
        (foreign_key,) = table.foreign_keys
        self.assertEqual(
            foreign_key.constraint.name,
            "fk_player_photos_player_id_players",
        )
        self.assertEqual(foreign_key.ondelete, "RESTRICT")
        self.assertEqual(
            {index.name for index in table.indexes},
            {"ix_player_photos_player_id"},
        )

    def test_player_photos_keep_upload_order(self):
        configure_mappers()
        self.assertEqual(
            [str(column) for column in Player.photos.property.order_by],
            ["player_photos.id"],
        )


class PlayerPhotoMigrationTests(unittest.TestCase):
    def test_migration_creates_and_drops_the_photo_table(self):
        migration = importlib.import_module(
            "migrations.versions.29ab530e58fc_add_player_photos"
        )
        self.assertEqual(migration.down_revision, "c7d8e9f0a1b2")

        with patch.object(migration, "op") as operation:
            migration.upgrade()
        create_call = operation.create_table.call_args
        self.assertEqual(create_call.args[0], "player_photos")
        columns = {
            item.name
            for item in create_call.args[1:]
            if hasattr(item, "name") and hasattr(item, "type")
        }
        self.assertEqual(
            columns,
            {"id", "player_id", "file_name", "content_type", "created_at"},
        )
        operation.create_index.assert_called_once_with(
            "ix_player_photos_player_id",
            "player_photos",
            ["player_id"],
        )

        with patch.object(migration, "op") as operation:
            migration.downgrade()
        operation.drop_index.assert_called_once()
        operation.drop_table.assert_called_once_with("player_photos")


if __name__ == "__main__":
    unittest.main()
