from policy_data.auth.codes import code_digest, generate_code, normalize_email


def test_code_is_six_digits_and_digest_is_context_bound() -> None:
    code = generate_code()
    assert len(code) == 6 and code.isdigit()
    pepper = b"p" * 32
    assert code_digest(pepper, "one", code) != code_digest(pepper, "two", code)


def test_email_normalization_changes_only_surrounding_space_and_domain_case() -> None:
    assert normalize_email(" Ada.Rossi@EXAMPLE.IT ") == "Ada.Rossi@example.it"
