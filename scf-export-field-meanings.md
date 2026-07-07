I deleted a bunch of fields we didn't need and added some new ones we needed since the last export and cleaned some stuff up. look at the scf-export json file in this directory for exact fields. This is an index of what each field is supposed to mean

music tracks: all the sub fields that are each music track on the release.
- disc number: what disc number the song is on
- track number: the track number the song is on
- title: song title
- duration_ms: duration in ms
- spotify_id: the specific song spotify ID
- highlight: whether or not I thought the song deserved a highlight aka I liked it much more than the other songs.
- explicit: whether or not the song is explicit.
music_rating: my music rating from 0 to 100
music_favorite: whether or not it was a favorite album of mine
music_length_ms: the total album length (added from music track total duration)
spotify_album_id the specific spotify id for this album
spotify_album_url: the url to the album on spotify
music_release_date: when that album was released canonically.
music_listened_at: when I listened to the album (copy from when the blog post was posted)
lastfm_release_id: the id of the album on last fm
music_total_tracks: the total # of tracks on this release
music_avg_track_ms: average track duration
music_explicit: are any songs marked explicit? if so then this should be marked true
music_mood_tags: basically the same thing as the top three tags on last fm.
unreleased: whether or not this release is unreleased from an artist.
listen-count-index: the number index of how many times this release has been listened too. Basically this will be 1 for most released except for the ones where I listened to an album twice, where the 2nd blog post would have this number at 2 instead. 