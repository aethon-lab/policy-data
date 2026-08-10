from __future__ import annotations

from policy_data.domain.enums import VotePosition

POSITION_MAP = {
    "Favorevole": VotePosition.YES,
    "Contrario": VotePosition.NO,
    "Astensione": VotePosition.ABSTAIN,
    "Non ha votato": VotePosition.DID_NOT_VOTE,
    "Non ha partecipato": VotePosition.NOT_PARTICIPATING,
    "In missione": VotePosition.MISSION,
    "In congedo": VotePosition.LEAVE,
    "In congedo o missione": VotePosition.LEAVE_OR_MISSION,
    "Richiedente che non vota": VotePosition.REQUESTER_NOT_VOTING,
    "Presidente di turno": VotePosition.PRESIDING,
    "Non in carica": VotePosition.NOT_IN_OFFICE,
    "Partecipazione a scrutinio segreto": VotePosition.SECRET_PARTICIPATION,
}


def normalize_vote_type(raw: str) -> str:
    value = raw.casefold()
    if "emendamento" in value:
        return "amendment"
    if "articolo" in value:
        return "article"
    if "fiducia" in value:
        return "confidence"
    if "final" in value:
        return "final"
    if "mozione" in value:
        return "motion"
    return "procedural"
