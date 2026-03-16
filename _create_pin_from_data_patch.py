def _create_pin_from_data(self, data: dict[str, Any]) -> Optional[Pin]:
    """Create Pin object from extracted data."""
    try:
        pin_id = str(data.get('id', ''))
        if not pin_id:
            return None
        
        image_url = data.get('image_url', '')
        if not image_url:
            return None
        
        title = str(data.get('title', '') or f"Pin_{pin_id}")
        description = str(data.get('description', title))
        
        return Pin(
            id=pin_id,
            title=title[:200],
            description=description[:500],
            media_url=image_url,
            media_type='image',
            original_filename=None,
            board_id='',
            created_at=''
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.debug(f"Failed to create pin: {e}")
        return None