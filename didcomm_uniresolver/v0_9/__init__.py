"""DID Resolution Protocol v0.9 message and handler definitions."""

import json
import logging
from datetime import datetime
from typing import Union

import aiohttp
from aries_cloudagent.messaging.agent_message import AgentMessage
from aries_cloudagent.messaging.base_handler import (
    BaseResponder, HandlerException, RequestContext
)
from aries_cloudagent.messaging.util import datetime_now, datetime_to_str
from aries_cloudagent.messaging.valid import INDY_ISO8601_DATETIME
from aries_cloudagent.protocols.didcomm_prefix import DIDCommPrefix
from aries_cloudagent.protocols.problem_report.v1_0.message import (
    ProblemReport, ProblemReportSchema
)
from aries_cloudagent.resolver.base import DIDNotFound
from marshmallow import fields

from ..acapy_tools import expand_message_class
from ..acapy_tools.awaitable_handler import (
    AwaitableErrorHandler, AwaitableHandler
)

LOGGER = logging.getLogger(__name__)


class DIDResolutionMessage(AgentMessage):
    """Base Message class for messages used for resolution."""
    protocol = "https://didcomm.org/did_resolution/0.9"


@expand_message_class
class ResolveDID(DIDResolutionMessage):
    """Class defining the structure of a resolve did message."""
    message_type = "resolve"

    # TODO: add further response fields/options, see
    # https://github.com/hyperledger/aries-rfcs/tree/master/features/0124-did-resolution-protocol#resolve-message

    class Fields:
        """Fields of ResolveDID message."""
        sent_time = fields.Str(
            required=False,
            description="Time message was sent, ISO8601 with space date/time separator",
            **INDY_ISO8601_DATETIME,
        )
        did = fields.Str(required=True, description="DID",
                         example="did:sov:WRfXPg8dantKVubE3HX8pw",)

    def __init__(
        self,
        *,
        sent_time: Union[str, datetime] = None,
        did: str = None,
        localization: dict = None,
        **kwargs,
    ):
        """
        TODO: update doc
        Initialize basic message object.
        Args:
            sent_time: Time message was sent
            did: did to resolve
            localization: localization
        """
        super().__init__(**kwargs)
        if not sent_time:
            sent_time = datetime_now()
        if localization:
            self._decorators["l10n"] = localization
        self.sent_time = datetime_to_str(sent_time)
        self.did = did

    @staticmethod
    async def resolve_did(did, resolver_url):
        """Resolve a DID using the uniresolver.

        resolver_url has to contain a {did} field.
        """
        url = resolver_url.format(did=did)
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status >= 200 and response.status < 400:
                    content = await response.json()
                    return content['didDocument']
                raise HandlerException(
                    f"Failed to resolve DID {did} using URL {url} with status "
                    "{response.status}"
                )

    async def handle(self, context: RequestContext, responder: BaseResponder):
        """Resolve a DID in response to a resolve message.

        Args:
            context: request context
            responder: responder callback
        """
        LOGGER.debug("ResolveDidHandler called with context %s", context)
        assert isinstance(context.message, ResolveDID)

        LOGGER.info("Received resolve did: %s", context.message.did)

        resolver_url = context.settings.get("didcomm_uniresolver.endpoint")
        try:
            did_document = await self.resolve_did(context.message.did, resolver_url)
        except Exception as err:
            LOGGER.error(str(err))
            msg = (f"Could not resolve DID {context.message.did} using service"
                   f" {resolver_url}")
            raise HandlerException(msg)
        else:
            reply_msg = ResolveDIDResult(did_document=did_document)
            reply_msg.assign_thread_from(context.message)
            if "l10n" in context.message._decorators:
                reply_msg._decorators["l10n"] = context.message._decorators["l10n"]
            await responder.send_reply(reply_msg)


@expand_message_class
class ResolveDIDResult(DIDResolutionMessage):
    """Class defining the structure of a resolve did message."""
    message_type = "resolve_result"

    class Fields:
        """Fields of ResolveDIDResult message."""
        sent_time = fields.Str(
            required=False,
            description="Time message was sent, ISO8601 with space date/time separator",
            **INDY_ISO8601_DATETIME,
        )
        did_document = fields.Dict(
            required=True,
            description="DID Document",
        )

    def __init__(
        self,
        *,
        sent_time: Union[str, datetime] = None,
        did_document: dict = None,
        localization: dict = None,
        **kwargs,
    ):
        """
        TODO: update doc
        Initialize basic message object.
        Args:
            sent_time: Time message was sent
            did_document: did document (as json or python object)
            localization: localization
        """
        super().__init__(**kwargs)
        if not sent_time:
            sent_time = datetime_now()
        if localization:
            self._decorators["l10n"] = localization
        self.sent_time = datetime_to_str(sent_time)
        self.did_document = did_document

    class Handler(AwaitableHandler):
        """Provider for handle to await response."""

        async def do_handle(self, context: RequestContext, responder: BaseResponder):
            """
            Message handler logic a did resolve result.

            Args:
                context: request context
                responder: responder callback
            """
            LOGGER.debug("ResolveDidResultHandler called with context %s", context)
            assert isinstance(context.message, ResolveDIDResult)

            LOGGER.info("Received resolve did document")
            LOGGER.debug("did document: %s", context.message.did_document)

            did_document = json.loads(context.message.did_document)

            await responder.send_webhook(
                "resolve_did_result",
                {
                    "connection_id": context.connection_record.connection_id,
                    "message_id": context.message._id,
                    "did_document": did_document,
                    "state": "received",
                },
            )


class ResolveDIDProblemReport(DIDResolutionMessage, ProblemReport):
    """Message for reporting errors from the remote resolver."""

    message_type = "problem-report"
    fields_from = ProblemReportSchema

    class Handler(AwaitableErrorHandler):
        """Handler for DID resolution problem reports."""

        async def do_handle(self, context: RequestContext, responder: BaseResponder):
            """Handle problem reports."""
            report: ResolveDIDProblemReport = context.message
            LOGGER.warning("Received problem report: %s", report.explain_ltxt)

        def map_exception(self, message: "ResolveDIDProblemReport"):
            """Map report message to an exception."""
            return DIDNotFound(
                f"DID not found on remote resolver: {message.explain_ltxt}"
            )


MESSAGE_TYPES = DIDCommPrefix.qualify_all({
    msg_class.Meta.message_type: '{}.{}'.format(msg_class.__module__, msg_class.__name__)
    for msg_class in [
        ResolveDID,
        ResolveDIDResult
    ]
})
