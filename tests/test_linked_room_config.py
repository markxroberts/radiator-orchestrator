"""Exercise actual options-flow validation and saved settings with HA UI stubs."""
import importlib.util
import types
import pytest
from conftest import ROOT, register


@pytest.fixture
def flow(controller_module):
    import sys
    sys.modules["homeassistant.core"].callback = lambda fn: fn
    class Base:
        def __init_subclass__(cls, **kwargs): pass
        def async_show_form(self, **kwargs): return kwargs
        def async_create_entry(self, **kwargs): return kwargs
    register("homeassistant.config_entries", ConfigFlow=Base, OptionsFlow=Base)
    class Select:
        def __init__(self, config): self.config = config
        def __call__(self, value): return value
    register("homeassistant.helpers.selector", SelectSelector=Select,
             SelectSelectorConfig=lambda **kwargs: kwargs)
    spec = importlib.util.spec_from_file_location("radiator_orchestrator.real_config_flow", ROOT / "config_flow.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    obj = module.RadiatorOptionsFlow()
    obj.config_entry = types.SimpleNamespace(data={"rooms": [
        {"name": "Bedroom", "room_type": "bedroom"},
        {"name": "Kitchen", "room_type": "living"},
        {"name": "Study"},
        {"name": "Bathroom", "room_type": "bathroom", "linked_rooms": ["Bedroom"],
         "linked_morning_preheat": True, "trv_internal_temp_compensation": True},
        {"name": "DHW", "room_type": "hot_water_only"},
    ]}, options={"unrelated": "preserved"})
    obj._selected_bathroom = "Bathroom"
    return obj


@pytest.mark.asyncio
async def test_selector_and_existing_defaults(flow):
    result = await flow.async_step_linked_bathroom_details()
    schema = result["data_schema"].schema
    selector = next(v for k, v in schema.items() if str(k) == "linked_rooms")
    assert selector.config == {"options": ["Bedroom", "Kitchen", "Study"], "multiple": True}
    key = next(k for k in schema if str(k) == "linked_rooms")
    assert key.description["suggested_value"] == ["Bedroom"]


@pytest.mark.asyncio
@pytest.mark.parametrize("links", [["Bedroom", "Kitchen", "Study"], ["Bedroom"], []])
async def test_save_and_clear_links_preserves_settings(flow, links):
    result = await flow.async_step_linked_bathroom_details({"linked_rooms": links})
    assert result["data"]["unrelated"] == "preserved"
    bath = result["data"]["rooms"][3]
    assert bath["linked_rooms"] == links
    assert bath["linked_morning_preheat"]
    assert bath["trv_internal_temp_compensation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("links", [["Bathroom"], ["DHW"], ["missing"], "Kitchen", None])
async def test_invalid_links_not_saved(flow, links):
    result = await flow.async_step_linked_bathroom_details({"linked_rooms": links})
    assert result["errors"] == {"base": "invalid_link"}
    assert flow._rooms[3]["linked_rooms"] == ["Bedroom"]


@pytest.mark.asyncio
async def test_rename_and_delete_living_link(flow):
    flow._rooms[3]["linked_rooms"] = ["Bedroom", "Kitchen"]
    flow._selected = 1
    result = await flow.async_step_edit_details({"name": "New kitchen", "room_type": "living"})
    assert result["data"]["rooms"][3]["linked_rooms"] == ["Bedroom", "New kitchen"]
    flow.config_entry.options = result["data"]
    result = await flow.async_step_remove_room({"index": "1"})
    assert result["data"]["rooms"][2]["linked_rooms"] == ["Bedroom"]
