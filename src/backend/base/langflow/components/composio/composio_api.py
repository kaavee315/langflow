from collections.abc import Sequence
from typing import Any

from composio.client.exceptions import NoItemsFound
from composio_langchain import Action, App, ComposioToolSet
from langchain_core.tools import Tool
from loguru import logger
from typing_extensions import override

from langflow.base.langchain_utilities.model import LCToolComponent
from langflow.inputs import DropdownInput, LinkInput, MessageTextInput, MultiselectInput, SecretStrInput, StrInput


class ComposioAPIComponent(LCToolComponent):
    display_name: str = "Composio Tools"
    description: str = "Use Composio toolset to run actions with your agent"
    name = "ComposioAPI"
    icon = "Composio"
    documentation: str = "https://docs.composio.dev"

    inputs = [
        MessageTextInput(name="entity_id", display_name="Entity ID", value="default", advanced=True),
        SecretStrInput(
            name="api_key",
            display_name="Composio API Key",
            required=True,
            refresh_button=True,
            info="Refer to https://docs.composio.dev/introduction/foundations/howtos/get_api_key",
        ),
        DropdownInput(
            name="app_names",
            display_name="App Name",
            options=list(App.__annotations__),
            value="",
            info="The app name to use. Please refresh after selecting app name",
            refresh_button=True,
        ),
        LinkInput(
            name="auth_link",
            display_name="Authentication Link",
            value="",
            info="Click to authenticate with the selected app",
            dynamic=True,
        ),
        StrInput(
            name="auth_status",
            display_name="Auth Status",
            value="Not Connected",
            info="Current authentication status",
            dynamic=True,
        ),
        MultiselectInput(
            name="action_names",
            display_name="Actions to use",
            required=True,
            options=[],
            value=[],
            info="The actions to pass to agent to execute",
            dynamic=True,
        ),
    ]

    def _check_for_authorization(self, app: str) -> str:
        """Checks if the app is authorized.

        Args:
            app (str): The app name to check authorization for.

        Returns:
            str: The authorization status.
        """
        toolset = self._build_wrapper()
        entity = toolset.client.get_entity(id=self.entity_id)
        try:
            entity.get_connection(app=app)
        except Exception:  # noqa: BLE001
            logger.opt(exception=True).debug("Authorization error")
            return self._handle_authorization_failure(toolset, entity, app)

        return f"{app} CONNECTED"

    def _handle_authorization_failure(self, toolset: ComposioToolSet, entity: Any, app: str) -> str:
        """Handles the authorization failure by attempting to process API key auth or initiate default connection.

        Args:
            toolset (ComposioToolSet): The toolset instance.
            entity (Any): The entity instance.
            app (str): The app name.

        Returns:
            str: The result of the authorization failure message.
        """
        try:
            auth_schemes = toolset.client.apps.get(app).auth_schemes
            if auth_schemes[0].auth_mode == "API_KEY":
                return self._process_api_key_auth(entity, app)
            return self._initiate_default_connection(entity, app)
        except Exception:  # noqa: BLE001
            logger.exception("Authorization error")
            return "Error"

    def _process_api_key_auth(self, entity: Any, app: str) -> str:
        """Processes the API key authentication.

        Args:
            entity (Any): The entity instance.
            app (str): The app name.

        Returns:
            str: The status of the API key authentication.
        """
        auth_status_config = self.auth_status_config
        is_url = "http" in auth_status_config or "https" in auth_status_config
        is_different_app = "CONNECTED" in auth_status_config and app not in auth_status_config
        is_default_api_key_message = "API Key" in auth_status_config

        if is_different_app or is_url or is_default_api_key_message:
            return "Enter API Key"
        if not is_default_api_key_message:
            entity.initiate_connection(
                app_name=app,
                auth_mode="API_KEY",
                auth_config={"api_key": self.auth_status_config},
                use_composio_auth=False,
                force_new_integration=True,
            )
            return f"{app} CONNECTED"
        return "Enter API Key"

    def _initiate_default_connection(self, entity: Any, app: str) -> str:
        connection = entity.initiate_connection(app_name=app, use_composio_auth=True, force_new_integration=True)
        return connection.redirectUrl

    def _get_connected_app_names_for_entity(self) -> list[str]:
        toolset = self._build_wrapper()
        connections = toolset.client.get_entity(id=self.entity_id).get_connections()
        return list({connection.appUniqueId for connection in connections})

    def _update_app_names_with_connected_status(self, build_config: dict) -> dict:
        connected_app_names = self._get_connected_app_names_for_entity()

        app_names = [
            f"{app_name}_CONNECTED" for app_name in App.__annotations__ if app_name.lower() in connected_app_names
        ]
        non_connected_app_names = [
            app_name for app_name in App.__annotations__ if app_name.lower() not in connected_app_names
        ]
        build_config["app_names"]["options"] = app_names + non_connected_app_names
        build_config["app_names"]["value"] = app_names[0] if app_names else ""
        return build_config

    def _get_normalized_app_name(self) -> str:
        return self.app_names.replace("_CONNECTED", "").replace("_connected", "")

    @override
    def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:
        if field_name == "api_key":
            if hasattr(self, "api_key") and self.api_key != "":
                build_config = self._update_app_names_with_connected_status(build_config)
            return build_config

        if field_name in {"app_names"} and hasattr(self, "api_key") and self.api_key != "":
            app_name = self._get_normalized_app_name()

            # Check auth status
            try:
                toolset = self._build_wrapper()
                entity = toolset.client.get_entity(id=self.entity_id)
                try:
                    entity.get_connection(app=app_name)
                    build_config["auth_status"]["value"] = f"{app_name} CONNECTED"
                    build_config["auth_link"]["value"] = ""  # Clear auth link when connected
                except NoItemsFound:
                    # Check if app uses API key auth
                    auth_schemes = toolset.client.apps.get(app_name).auth_schemes
                    if auth_schemes[0].auth_mode == "API_KEY":
                        build_config["auth_status"]["value"] = "Enter API Key"
                        build_config["auth_link"]["value"] = ""  # No link needed for API key auth
                    else:
                        # Generate OAuth auth URL
                        auth_url = self._initiate_default_connection(entity, app_name)
                        build_config["auth_link"]["value"] = auth_url
                        build_config["auth_status"]["value"] = "Click link to authenticate"
            except Exception as e:  # noqa: BLE001
                logger.error(f"Error checking auth status: {e}")
                build_config["auth_link"]["value"] = ""
                build_config["auth_status"]["value"] = f"Error: {e!s}"

            # Update action names
            all_action_names = list(Action.__annotations__)
            app_action_names = [
                action_name
                for action_name in all_action_names
                if action_name.lower().startswith(app_name.lower() + "_")
            ]
            build_config["action_names"]["options"] = app_action_names
            build_config["action_names"]["value"] = [app_action_names[0]] if app_action_names else [""]

        return build_config

    def build_tool(self) -> Sequence[Tool]:
        composio_toolset = self._build_wrapper()
        return composio_toolset.get_tools(actions=self.action_names)

    def _build_wrapper(self) -> ComposioToolSet:
        return ComposioToolSet(api_key=self.api_key)
