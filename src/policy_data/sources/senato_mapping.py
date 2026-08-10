from __future__ import annotations

from policy_data.domain.enums import VotePosition

POSITION_MAP = {
    "favorevole": VotePosition.YES,
    "contrario": VotePosition.NO,
    "astenuto": VotePosition.ABSTAIN,
    "inCongedoMissione": VotePosition.LEAVE_OR_MISSION,
    "presenteNonVotante": VotePosition.PRESENT_NOT_VOTING,
    "richiedenteNonVotante": VotePosition.REQUESTER_NOT_VOTING,
    "presidente": VotePosition.PRESIDING,
}


def predicate_local_name(value: str) -> str:
    return value.rstrip("/").rsplit("/", 1)[-1]
