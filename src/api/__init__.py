from subprocess import Popen, PIPE, run

# args: list = ["python", "-b", "api/teste_case/__init__.py"]
# process = Popen(
#     args,          # Command and arguments as a list
#     stdin=PIPE, # Optional: handle input
#     stdout=PIPE,# Optional: capture output
#     stderr=PIPE,# Optional: capture errors
#     text=True              # Return strings instead of bytes
# )
# output, errors = process.communicate()  # Interact with the process
# return_code = process.returncode        # Get exit status

# print(f"""
#       Output:
#       {output}
#       Errors:
#       {errors}
#         Return Code:
#         {return_code}
#       """)


# resultado = run(args, capture_output=True, text=True)

# Exibe a saída do comando
# print("Saída:", resultado.stdout)
# print("Erro:", resultado.stderr)