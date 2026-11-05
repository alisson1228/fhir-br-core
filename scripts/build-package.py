
#!/usr/bin/env python3

import base64
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile

from pathlib import Path


# ============================================================
# CONFIGURAÇÕES
# ============================================================

URL = "https://hl7.org.br/fhir/core/full-ig.zip"

PACKAGE_PATH = "site/package.r4.tgz"

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "fhir-packages"
OUTPUT_FILE = OUTPUT_DIR / "package.r4.json"

BLOCK_SIZE = 1024 * 1024
CACHE_LIMIT = 8
CHUNK_SIZE = 256 * 1024 - 1


# ============================================================
# INTERFACE VISUAL
# ============================================================

class UI:

    @staticmethod
    def title():
        print()
        print("=" * 60)
        print("       FHIR BR CORE - PACKAGE BUILDER")
        print("=" * 60)
        print()

    @staticmethod
    def stage(number, message):
        print()
        print(f"[{number}/5] {message}")
        print("-" * 60)

    @staticmethod
    def progress(current, total, label):
        percent = current / total if total else 0
        filled = int(30 * percent)

        bar = "#" * filled + "-" * (30 - filled)

        print(
            f"\r{label} [{bar}] {percent * 100:6.2f}% "
            f"({current / 1024 / 1024:.2f} / "
            f"{total / 1024 / 1024:.2f} MB)",
            end="",
            flush=True
        )

        if current >= total:
            print()

    @staticmethod
    def success(message):
        print(f"\n[OK] {message}")

    @staticmethod
    def error(message):
        print(f"\n[ERRO] {message}")


# ============================================================
# LEITURA REMOTA DO ZIP
# ============================================================

class RemoteZip:

    def __init__(self, url):

        self.url = url
        self.position = 0
        self.cache = {}

        request = urllib.request.Request(
            url,
            method="HEAD"
        )

        with urllib.request.urlopen(request) as response:
            self.size = int(response.headers["Content-Length"])

            self.etag = response.headers.get("ETag")

        self.downloaded = 0

        self.cache_order = []

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):

        if whence == io.SEEK_SET:
            self.position = offset

        elif whence == io.SEEK_CUR:
            self.position += offset

        elif whence == io.SEEK_END:
            self.position = self.size + offset

        if self.position < 0:
            raise ValueError("Posição inválida")

        return self.position

    def fetch_block(self, start):

        if start in self.cache:
            return self.cache[start]

        end = min(
            start + BLOCK_SIZE - 1,
            self.size - 1
        )

        headers = {
            "Range": f"bytes={start}-{end}"
        }

        if self.etag:
            headers["If-Range"] = self.etag

        request = urllib.request.Request(
            self.url,
            headers=headers
        )

        with urllib.request.urlopen(request) as response:

            if response.status != 206:
                raise RuntimeError(
                    "Servidor não aceitou HTTP Range."
                )

            content_range = response.headers.get(
                "Content-Range", ""
            )

            if not re.fullmatch(
                rf"bytes {start}-{end}/{self.size}",
                content_range
            ):
                raise RuntimeError(
                    f"Content-Range inválido: {content_range}"
                )

            data = response.read()

        self.downloaded += len(data)

        self.cache[start] = data
        self.cache_order.append(start)

        if len(self.cache_order) > CACHE_LIMIT:
            old = self.cache_order.pop(0)
            del self.cache[old]

        print(
            f"\rConsultando ZIP: "
            f"{self.downloaded / 1024:.1f} KB recebidos",
            end="",
            flush=True
        )

        return data

    def read(self, size=-1):

        if size is None or size < 0:
            size = self.size - self.position

        size = min(
            size,
            self.size - self.position
        )

        result = bytearray()

        while size > 0:

            block_start = (
                self.position // BLOCK_SIZE
            ) * BLOCK_SIZE

            block = self.fetch_block(block_start)

            offset = self.position - block_start

            amount = min(
                size,
                len(block) - offset
            )

            if amount <= 0:
                break

            result.extend(
                block[offset:offset + amount]
            )

            self.position += amount
            size -= amount

        return bytes(result)


# ============================================================
# PROCESSAMENTO
# ============================================================

def main():

    UI.title()

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    with tempfile.TemporaryDirectory() as temp_dir:

        temp_dir = Path(temp_dir)

        package_temp = temp_dir / "package.r4.tgz"

        output_temp = OUTPUT_DIR / "package.r4.json.tmp"

        try:

            # ------------------------------------------------
            # 1. CONEXÃO
            # ------------------------------------------------

            UI.stage(1, "Conectando ao HL7 Brasil")

            remote_zip = RemoteZip(URL)

            print(
                f"Arquivo remoto: "
                f"{remote_zip.size / 1024 / 1024:.2f} MB"
            )

            UI.success("Conexão estabelecida.")

            # ------------------------------------------------
            # 2. LOCALIZAR PACOTE
            # ------------------------------------------------

            UI.stage(2, "Localizando pacote FHIR R4")

            with zipfile.ZipFile(remote_zip) as archive:

                try:
                    package = archive.getinfo(PACKAGE_PATH)

                except KeyError:
                    raise RuntimeError(
                        f"Pacote não encontrado: {PACKAGE_PATH}"
                    )

                print(f"Arquivo: {PACKAGE_PATH}")

                print(
                    f"Tamanho: "
                    f"{package.file_size / 1024 / 1024:.2f} MB"
                )

                UI.success("Pacote localizado.")

                # --------------------------------------------
                # 3. EXTRAIR PACOTE
                # --------------------------------------------

                UI.stage(3, "Baixando e extraindo pacote")

                with archive.open(package) as source:

                    with open(package_temp, "wb") as output:

                        downloaded = 0

                        while True:

                            chunk = source.read(CHUNK_SIZE)

                            if not chunk:
                                break

                            output.write(chunk)

                            downloaded += len(chunk)

                            UI.progress(
                                downloaded,
                                package.file_size,
                                "Extraindo"
                            )

                UI.success("Pacote extraído.")

            # ------------------------------------------------
            # 4. VALIDAR E CONVERTER BASE64
            # ------------------------------------------------

            UI.stage(4, "Validando e convertendo para Base64")

            with tarfile.open(
                package_temp,
                "r:gz"
            ) as tar:

                tar.getmembers()

            print("Integridade do TAR.GZ verificada.")

            original_size = package_temp.stat().st_size

            # ------------------------------------------------
            # 5. GERAR JSON
            # ------------------------------------------------

            UI.stage(5, "Gerando arquivo JSON FHIR")

            with open(package_temp, "rb") as source:

                with open(
                    output_temp,
                    "w",
                    encoding="utf-8"
                ) as output:

                    output.write(
                        '{\n'
                        '    "resourceType": "Parameters",\n'
                        '    "parameter": [\n'
                        '        {\n'
                        '            "name": "npmContent",\n'
                        '            "valueBase64Binary": "'
                    )

                    processed = 0

                    while True:

                        # Mantém os blocos múltiplos de 3 bytes
                        chunk = source.read(CHUNK_SIZE)

                        if not chunk:
                            break

                        encoded = base64.b64encode(
                            chunk
                        ).decode("ascii")

                        output.write(encoded)

                        processed += len(chunk)

                        UI.progress(
                            processed,
                            original_size,
                            "Base64"
                        )

                    output.write(
                        '"\n'
                        '        }\n'
                        '    ]\n'
                        '}\n'
                    )

            # Confere se o JSON gerado é válido
            with open(output_temp, "r", encoding="utf-8") as file:
                json.load(file)

            os.replace(output_temp, OUTPUT_FILE)

            # Remove pacote intermediário antigo, se existir
            old_package = OUTPUT_DIR / "package.r4.tgz"

            if old_package.exists():
                old_package.unlink()

            # ------------------------------------------------
            # RESULTADO
            # ------------------------------------------------

            final_size = OUTPUT_FILE.stat().st_size

            print()
            print("=" * 60)
            print("             PROCESSO CONCLUÍDO!")
            print("=" * 60)

            print(f"Arquivo: {OUTPUT_FILE}")
            print(
                f"Tamanho original: "
                f"{original_size / 1024 / 1024:.2f} MB"
            )
            print(
                f"Tamanho JSON: "
                f"{final_size / 1024 / 1024:.2f} MB"
            )

            print()
            UI.success("Package FHIR convertido com sucesso!")

        except Exception as error:

            if output_temp.exists():
                output_temp.unlink()

            UI.error(str(error))

            raise SystemExit(1)


if __name__ == "__main__":
    main()
