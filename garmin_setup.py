"""
MANU///LOGS — Garmin Connect: configuración inicial
Corré una vez: python garmin_setup.py
Guarda los tokens en ~/.garth para reutilización automática.
"""

import os
import getpass

GARTH_TOKEN_DIR = os.path.expanduser("~/.garth")


def setup_garmin():
    print("\n" + "=" * 50)
    print("  GARMIN CONNECT — Configuración inicial")
    print("=" * 50)

    email = input("\n  Email de Garmin Connect: ").strip()
    password = getpass.getpass("  Contraseña: ")

    print("\n  Autenticando...")

    from garminconnect import Garmin

    api = Garmin(email, password)
    api.login()

    # Guardar tokens para reutilización
    api.garth.dump(GARTH_TOKEN_DIR)

    print(f"\n  Tokens guardados en {GARTH_TOKEN_DIR}")
    print("  Ya podés correr server.py — los datos de running se cargan automáticamente.")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    setup_garmin()
