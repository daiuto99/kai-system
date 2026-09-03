import audit_trail


def test_colon_task_id_is_rejected():
    # A crafted id carrying the reserved ':consult:' separator must not validate —
    # otherwise it would surface inside another task's trail as a forged delegation.
    assert audit_trail._TASK_ID.fullmatch("A:consult:B") is None
    assert audit_trail._TASK_ID.fullmatch("task:A:consult:B") is None


def test_plain_task_ids_still_valid():
    for good in ("job-123", "abc.def_ghi", "A", "9f3c2b1a-0000-1111"):
        assert audit_trail._TASK_ID.fullmatch(good) is not None
