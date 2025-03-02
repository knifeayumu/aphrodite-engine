"""
This file contains the command line arguments for the Aphrodite's
OpenAI-compatible server. It is kept in a separate file for documentation
purposes.
"""

import argparse
import json
import ssl

from aphrodite.common.utils import FlexibleArgumentParser
from aphrodite.endpoints.openai.serving_engine import (LoRAModulePath,
                                                       PromptAdapterPath)
from aphrodite.endpoints.openai.tool_parsers import ToolParserManager
from aphrodite.engine.args_tools import AsyncEngineArgs


class LoRAParserAction(argparse.Action):

    def __call__(self, parser, namespace, values, option_string=None):
        lora_list = []
        for item in values:
            if item in [None, '']:  # Skip if item is None or empty string
                continue
            if '=' in item and ',' not in item:  # Old format: name=path
                name, path = item.split('=')
                lora_list.append(LoRAModulePath(name, path))
            else:  # Assume JSON format
                try:
                    lora_dict = json.loads(item)
                    lora = LoRAModulePath(**lora_dict)
                    lora_list.append(lora)
                except json.JSONDecodeError:
                    parser.error(
                        f"Invalid JSON format for --lora-modules: {item}")
                except TypeError as e:
                    parser.error(
                        f"Invalid fields for --lora-modules: {item} - {str(e)}"
                    )
        setattr(namespace, self.dest, lora_list)


class PromptAdapterParserAction(argparse.Action):

    def __call__(self, parser, namespace, values, option_string=None):
        adapter_list = []
        for item in values:
            name, path = item.split('=')
            adapter_list.append(PromptAdapterPath(name, path))
        setattr(namespace, self.dest, adapter_list)


def make_arg_parser(parser: FlexibleArgumentParser) -> FlexibleArgumentParser:
    parser.add_argument("--host", type=str, default=None, help="host name")
    parser.add_argument("--port", type=int, default=2242, help="port number")
    parser.add_argument(
        "--uvicorn-log-level",
        type=str,
        default="info",
        choices=['debug', 'info', 'warning', 'error', 'critical', 'trace'],
        help="log level for uvicorn")
    parser.add_argument("--allow-credentials",
                        action="store_true",
                        help="allow credentials")
    parser.add_argument("--allowed-origins",
                        type=json.loads,
                        default=["*"],
                        help="allowed origins")
    parser.add_argument("--allowed-methods",
                        type=json.loads,
                        default=["*"],
                        help="allowed methods")
    parser.add_argument("--allowed-headers",
                        type=json.loads,
                        default=["*"],
                        help="allowed headers")
    parser.add_argument("--api-keys",
                        type=str,
                        default=None,
                        help="If provided, the server will require this key "
                        "to be presented in the header.")
    parser.add_argument("--admin-key",
                        type=str,
                        default=None,
                        help="If provided, the server will require this key "
                        "to be presented in the header for admin operations.")
    parser.add_argument(
        "--lora-modules",
        type=str,
        default=None,
        nargs='+',
        action=LoRAParserAction,
        help="LoRA module configurations in either 'name=path' format"
        "or JSON format. "
        "Example (old format): 'name=path' "
        "Example (new format): "
        "'{\"name\": \"name\", \"local_path\": \"path\", "
        "\"base_model_name\": \"id\"}'")
    parser.add_argument(
        "--prompt-adapters",
        type=str,
        default=None,
        nargs='+',
        action=PromptAdapterParserAction,
        help="Prompt adapter configurations in the format name=path. "
        "Multiple adapters can be specified.")
    parser.add_argument("--chat-template",
                        type=str,
                        default=None,
                        help="The file path to the chat template, "
                        "or the template in single-line form "
                        "for the specified model")
    parser.add_argument("--response-role",
                        type=str,
                        default="assistant",
                        help="The role name to return if "
                        "`request.add_generation_prompt=true`.")
    parser.add_argument("--ssl-keyfile",
                        type=str,
                        default=None,
                        help="The file path to the SSL key file")
    parser.add_argument("--ssl-certfile",
                        type=str,
                        default=None,
                        help="The file path to the SSL cert file")
    parser.add_argument("--ssl-ca-certs",
                        type=str,
                        default=None,
                        help="The CA certificates file")
    parser.add_argument(
        "--ssl-cert-reqs",
        type=int,
        default=int(ssl.CERT_NONE),
        help="Whether client certificate is required (see stdlib ssl module's)"
    )
    parser.add_argument(
        "--root-path",
        type=str,
        default=None,
        help="FastAPI root_path when app is behind a path based routing proxy")
    parser.add_argument(
        "--middleware",
        type=str,
        action="append",
        default=[],
        help="Additional ASGI middleware to apply to the app. "
        "We accept multiple --middleware arguments. "
        "The value should be an import path. "
        "If a function is provided, Aphrodite will add it to the server "
        "using @app.middleware('http'). "
        "If a class is provided, Aphrodite will add it to the server "
        "using app.add_middleware(). ")
    parser.add_argument(
        "--launch-kobold-api",
        action="store_true",
        help="Launch the Kobold API server alongside the OpenAI server")
    parser.add_argument("--max-log-len",
                        type=int,
                        default=0,
                        help="Max number of prompt characters or prompt "
                        "ID numbers being printed in log."
                        "\n\nDefault: 0")
    parser.add_argument(
        "--return-tokens-as-token-ids",
        action="store_true",
        help="When --max-logprobs is specified, represents single tokens as"
        "strings of the form 'token_id:{token_id}' so that tokens that"
        "are not JSON-encodable can be identified.")
    parser.add_argument(
        "--disable-frontend-multiprocessing",
        action="store_true",
        help="If specified, will run the OpenAI frontend server in the same "
        "process as the model serving engine.")
    parser.add_argument(
        "--allow-inline-model-loading",
        action="store_true",
        help="If specified, will allow the model to be switched inline "
        "in the same process as the OpenAI frontend server.")
    parser.add_argument(
        "--enable-auto-tool-choice",
        action="store_true",
        default=False,
        help=
        "Enable auto tool choice for supported models. Use --tool-call-parser"
        "to specify which parser to use")
    valid_tool_parsers = ToolParserManager.tool_parsers.keys()
    parser.add_argument(
        "--tool-call-parser",
        type=str,
        metavar="{" + ",".join(valid_tool_parsers) + "} or name registered in "
        "--tool-parser-plugin",
        default=None,
        help=
        "Select the tool call parser depending on the model that you're using."
        " This is used to parse the model-generated tool call into OpenAI API "
        "format. Required for --enable-auto-tool-choice.")
    parser.add_argument(
        "--tool-parser-plugin",
        type=str,
        default="",
        help=
        "Specify the tool parser plugin path to parse model-generated tool "
        "calls into OpenAI API format. The parsers registered in this plugin "
        "can be referenced in --tool-call-parser.")

    parser = AsyncEngineArgs.add_cli_args(parser)
    return parser


def create_parser_for_docs() -> FlexibleArgumentParser:
    parser_for_docs = FlexibleArgumentParser(
        prog="-m aphrodite.endpoints.openai.api_server")
    return make_arg_parser(parser_for_docs)
