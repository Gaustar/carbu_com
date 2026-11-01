async def async_setup_entry(hass, config_entry, async_add_entities):
    from .conseil import async_setup_numbers
    await async_setup_numbers(hass, config_entry, async_add_entities)
