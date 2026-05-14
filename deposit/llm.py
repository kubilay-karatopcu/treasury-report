"""
LangChain wrappers: one for each LLM task.

  extract_request(text)  -> PricingRequestExtract   (structured)
  check_revision(text)   -> RevisionCheckResult     (structured)
  generate_response(req) -> str                     (free-form Turkish)
"""

import os
import json
import logging
from dataclasses import asdict, is_dataclass
from typing import Optional

import httpx
from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.output_parsers import PydanticOutputParser

from .prompts import (
    EXTRACT_REQUEST_SYSTEM,
    REVISION_CHECK_SYSTEM,
    GENERATE_RESPONSE_SYSTEM,
)
from .state import PricingRequest

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# LLM client
# --------------------------------------------------------------------------- #

DEFAULT_LLM_URL = os.getenv(
    "LLM_API_URL",
    "https://smg-llm-api.seip-vip-prd-ocpgen11.qnb.com.tr/v1",
)
DEFAULT_MODEL   = os.getenv("LLM_MODEL",   "qwen-3.5-27b")
DEFAULT_API_KEY = os.getenv("LLM_API_KEY", "")


def get_llm(temperature: float = 0.0) -> ChatOpenAI:
    return ChatOpenAI(
        model=DEFAULT_MODEL,
        openai_api_base=DEFAULT_LLM_URL,
        openai_api_key=DEFAULT_API_KEY,
        temperature=temperature,
        http_client=httpx.Client(verify=False),
    )


# --------------------------------------------------------------------------- #
# Pydantic schemas
# --------------------------------------------------------------------------- #

class PricingRequestExtract(BaseModel):
    cust_id:  Optional[int]   = Field(default=0,  description="Müşteri no")
    tenor:    Optional[int]   = Field(default=0,  description="Vade (gün)")
    amount:   Optional[float] = Field(default=0,  description="Tutar")
    currency: Optional[str]   = Field(default="", description="3-harfli kod")


class RevisionCheckResult(BaseModel):
    is_price_request:     bool  = Field(default=False)
    is_acceptance:        bool  = Field(default=False,
                                        description="User thanked / accepted / approved the price")
    demanded_price:       float = Field(default=0.0)
    revision_probability: float = Field(default=0.0,
                                        description="0.0-1.0, how justified the revision is")
    amount_change:        float = Field(default=0.0,
                                        description="Additional amount (not new total)")
    tenor_change:         int   = Field(default=0,
                                        description="New tenor if changed, else 0")


# --------------------------------------------------------------------------- #
# Chain 1: extract request fields
# --------------------------------------------------------------------------- #

_extract_parser = PydanticOutputParser(pydantic_object=PricingRequestExtract)


def extract_request(text: str) -> PricingRequestExtract:
    try:
        messages = [
            SystemMessage(content=EXTRACT_REQUEST_SYSTEM + "\n\n" +
                          _extract_parser.get_format_instructions()),
            HumanMessage(content=text),
        ]
        raw = get_llm(0.0).invoke(messages)
        result = _extract_parser.invoke(raw)
        log.info("extract_request → %s", result.model_dump())
        return result
    except Exception as e:
        log.warning("extract_request failed: %s", e)
        return PricingRequestExtract()


# --------------------------------------------------------------------------- #
# Chain 2: revision check
# --------------------------------------------------------------------------- #

_revision_parser = PydanticOutputParser(pydantic_object=RevisionCheckResult)


def check_revision(text: str) -> RevisionCheckResult:
    try:
        messages = [
            SystemMessage(content=REVISION_CHECK_SYSTEM + "\n\n" +
                          _revision_parser.get_format_instructions()),
            HumanMessage(content=text),
        ]
        raw = get_llm(0.0).invoke(messages)
        result = _revision_parser.invoke(raw)
        log.info("check_revision → %s", result.model_dump())
        return result
    except Exception as e:
        log.warning("check_revision failed: %s", e)
        return RevisionCheckResult()


# --------------------------------------------------------------------------- #
# Chain 3: generate Turkish response
# --------------------------------------------------------------------------- #

def generate_response(req: PricingRequest, context_note: str = "") -> str:
    # Serialise defensively: dataclass → dict, dict → dict, else str().
    if is_dataclass(req):
        payload = asdict(req)
    elif isinstance(req, dict):
        payload = req
    elif hasattr(req, "model_dump"):          # pydantic
        payload = req.model_dump()
    else:
        payload = {"value": str(req)}

    # Keep only the fields the prompt expects (defensive against extras).
    keep = ("cust_id", "tenor", "amount", "currency", "price",
            "pricing_no", "previous_price", "previous_query")
    payload = {k: payload.get(k) for k in keep if k in payload}

    if context_note:
        payload["context_note"] = context_note

    try:
        messages = [
            SystemMessage(content=GENERATE_RESPONSE_SYSTEM),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False,
                                            default=str)),
        ]
        reply = get_llm(0.05).invoke(messages)
        # ChatOpenAI returns an AIMessage; .content is the string.
        text = reply.content if hasattr(reply, "content") else str(reply)
        return (text or "").strip()
    except Exception as e:
        log.warning("generate_response failed: %s", e)
        return ("Üzgünüm, şu an yanıt üretmekte sorun yaşıyorum. "
                "Lütfen tekrar dener misiniz?")
