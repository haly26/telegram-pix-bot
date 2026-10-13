
from app import executar_rotina_diaria, init_db


if __name__ == "__main__":
    print("INICIANDO ROTINA DE ASSINATURAS...", flush=True)

    # Confirma que o banco está acessível antes de executar a rotina.
    init_db()

    executar_rotina_diaria()

    print("ROTINA DE ASSINATURAS CONCLUIDA.", flush=True)
