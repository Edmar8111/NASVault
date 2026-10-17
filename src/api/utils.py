
import jwt
import datetime
import secrets
from functools import wraps
from fastapi import HTTPException, status

# ============================================================
# CHAVE SECRETA
# ============================================================

# IMPORTANTE:
# Esta chave precisa permanecer a mesma enquanto os tokens
# estiverem sendo utilizados.
#
# Em produção, utilize uma variável de ambiente.
SECRET_KEY = secrets.token_hex(32)
ALGORITHM = "HS256"

# ============================================================
# WRAPPER COMPLETO
# ============================================================

def gerenciar_token(func):

    @wraps(func)
    def wrapper(user_id, username, token=None, *args, **kwargs):

        # ----------------------------------------------------
        # 1. VERIFICA SE EXISTE TOKEN
        # ----------------------------------------------------

        if token:

            try:

                # ------------------------------------------------
                # 2. DECODIFICA E VALIDA O TOKEN
                #
                # jwt.decode verifica automaticamente:
                #
                # - assinatura
                # - estrutura
                # - expiração através do campo "exp"
                # ------------------------------------------------

                payload = jwt.decode(
                    token,
                    SECRET_KEY,
                    algorithms=[ALGORITHM]
                )

                # Se chegou aqui, o token existe e é válido.
                print("Token válido encontrado.")

                # Executa a função original reutilizando
                # o token existente.
                return func(
                    user_id=user_id,
                    username=username,
                    token=token,
                    payload=payload,
                    *args,
                    **kwargs
                )


            # ----------------------------------------------------
            # 3. TOKEN EXPIRADO
            # ----------------------------------------------------

            except jwt.ExpiredSignatureError:

                print("Token expirado. Gerando um novo token.")


            # ----------------------------------------------------
            # 4. TOKEN INVÁLIDO
            # ----------------------------------------------------

            except jwt.InvalidTokenError:

                print("Token inválido. Gerando um novo token.")


        else:

            # Não existe token.
            print("Nenhum token encontrado. Gerando novo token.")


        # ----------------------------------------------------
        # 5. CRIA UM NOVO TOKEN
        # ----------------------------------------------------

        exp = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=15)
        )

        # Dados que serão armazenados no JWT.
        payload = {
            "user_id": user_id,
            "username": username,
            "exp": exp
        }

        # Cria um novo token.
        novo_token = jwt.encode(
            payload,
            SECRET_KEY,
            algorithm=ALGORITHM
        )

        print("Novo token criado.")

        # ----------------------------------------------------
        # 6. EXECUTA A FUNÇÃO ORIGINAL
        # ----------------------------------------------------

        return func(
            user_id=user_id,
            username=username,
            token=novo_token,
            payload=payload,
            *args,
            **kwargs
        )

    return wrapper


# ============================================================
# FUNÇÃO PRINCIPAL
# ============================================================

@gerenciar_token
def gerar_ou_validar_token(
    user_id,
    username,
    token=None,
    payload=None
):

    return {
        "user_id": user_id,
        "username": username,
        "token": token,
        "payload": payload
    }

def validar_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expirado.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.")



from functools import wraps
from time import perf_counter


def medir_tempo(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        inicio = perf_counter()
        resultado = func(*args, **kwargs)
        fim = perf_counter()
        print(f"Tempo de execução de {func.__name__}: {fim - inicio:.4f} segundos")
        return resultado
    return wrapper

# ============================================================
# EXECUÇÃO
# ============================================================

# resultado = gerar_ou_validar_token(
#     user_id=1,
#     username="edmar"
# )

# print("\nRESULTADO:")
# print(resultado)


# # Guarda o token retornado.
# token_existente = resultado["token"]


# ============================================================
# SEGUNDA EXECUÇÃO
#
# Agora enviamos o token já existente.
# O wrapper irá validar e reutilizar o token.
# ============================================================

# resultado = gerar_ou_validar_token(
#     user_id=1,
#     username="edmar",
#     token="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoxLCJ1c2VybmFtZSI6ImVkbWFyIiwiZXhwIjoxNzg5MTAxMjQ4fQ.5k30o8IafWk-J8OGp27XIGl1BDeFWeR0cSjhvw5ugdo"
# )

# print("\nRESULTADO COM TOKEN EXISTENTE:")
# print(resultado)
