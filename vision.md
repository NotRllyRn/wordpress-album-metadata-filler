# Vision

This repository is just a cli python tool that lets the user connect to their wordpress data base and fill out scf data on each blog post.

Basically every blog post on this webiste is an album. The title is the album name canonically and stripped for illegal wordpress characters. The tags are every artist for that album, the category is used for the type of release; such as album, single, EP, or compilation.

All of the data is currently based off of spotify data which is fine and we will keep it that way because we will be locally downloading the meta data our selves and add that to each blog post.

scf-export-2026-06-18.json is the export for all the scf fields. I want EVERY single field to be filled out.

this is how this is going to work.

when the CLI is running it'll allow the user to input how many posts to process (with an all option as well). This tool has to be very verbose so that I can make sure that it's not doing anything wrong.

When it runs it will get the album data from wordpress it self. The release type, the artist tags, and the release title. Then it will fuzzy search against spotify to find the same exact release on spotify. It will then pull as much data from spotify as possible to fill in the scf values. For the values that you cannot find and are not accurate such as genre and etc, the program will find that same release on LastFM in a similar manner to how it found it on spotify, double confirming that it's the same release on LastFM using that API key. Then it will use the top 3 tags for the genre. Then it will fill out the rest of the fields.

^ if you have improvements then please let me know.

So go through the export and figure out exactly where you can find each field so that you can make sure each release that gets process get's 100% filled out. If you have questions on certain fields then please let me know and ask me exactly what it does.

The cli will also have a dry run that instead saves the output to a json file to show what it would have changed and done so that I can verify what it does before it's permanently changed on the wordpress end. 

album-art-picker-v2-analysis shows you how another program interacts with the spotify api to search for things along with recommendations. ignore the talk about wordpress because another analysis gives you this.

spotify-album-blog-tracker-analysis-report shows you how another program interacts with wordpress it self and some other details to how it heuristically determines releases as well.

Also it while it adds data to the metadata field in scf, it will also add data to the taxonomies like Artists, Genres, and Release Types.

For Artists, you can just copy and paste the tags that are currently on the post into that field taxonomy. Or you can pull them from the spotify search when you do it.

For Genres, you are pulling this from LastFm and putting in the top 3 you find.

For Release type, confirm the heuristic matches with what is currently in the taxonomy that is on the actual wordpress post. Then add that to the Release Type as well. I want you to note something however, some posts have multiple taxonies and that is not multiple release types. Only one of the fields is actually the release type, I also use that field to mark a post as a "Relisten", "Unreleased", or "Concert" Relisten and Unreleased already have their own fields in the metadata scf so that's where you will mark it. For now we are not going to do anything about that Concert issue.