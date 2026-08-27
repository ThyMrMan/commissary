#!/usr/bin/env python3

"""
Commissary Database Module

This module provides database functionality for storing and managing
music library metadata from Plex. It includes:

- SQLite database management for artists, albums, and tracks
- Singleton database access pattern
- Data models for database entities
- Search and query capabilities

Usage:
    from database import get_database
    
    db = get_database()
    stats = db.get_statistics()
"""

from .music_database import (
    MusicDatabase,
    DatabaseArtist,
    DatabaseAlbum, 
    DatabaseTrack,
    get_database,
    close_database
)

__all__ = [
    'MusicDatabase',
    'DatabaseArtist',
    'DatabaseAlbum',
    'DatabaseTrack', 
    'get_database',
    'close_database'
]

__version__ = '2.2.0'