import json
from datetime import datetime, timedelta
from collections import defaultdict
import apachelogs
import geoip2.database
import jinja2
import os
import re
from urllib.parse import urlparse, unquote, parse_qs
from user_agents import parse as ua_parse

def clean_post_name(path, prefixes):
    """Remove the prefix from the blog post path and decode URL-encoded characters."""
    for prefix in prefixes:
        if path.startswith(prefix):
            post_name = path[len(prefix):]
            return unquote(post_name)
    return path

def get_recent_log_files(base_log_name, directory, days_back=7):
    """Find log files from the last 7 days based on the date in the filename."""
    seven_days_ago = datetime.now().date() - timedelta(days=days_back)
    log_files = []
    # Regex to match logname-YYYYMMDD
    pattern = re.compile(rf"{re.escape(base_log_name)}-\d{{8}}$")
    
    for filename in os.listdir(directory):
        if pattern.match(filename):
            # Extract date from filename (e.g., 20250223)
            date_str = filename.split('-')[-1]
            try:
                log_date = datetime.strptime(date_str, '%Y%m%d').date()
                if log_date >= seven_days_ago:
                    log_files.append(os.path.join(directory, filename))
            except ValueError:
                continue  # Skip if date parsing fails
    
    # Sort by date (newest first)
    log_files.sort(key=lambda x: x.split('-')[-1], reverse=True)
    return log_files

def main():
    # Load configuration
    with open('config.json') as f:
        config = json.load(f)

    # Initialize GeoIP reader
    reader = geoip2.database.Reader(config['geoip_db'])

    # Time filter for the last 7 days
    seven_days_ago = datetime.now() - timedelta(days=7)

    # Get base log name and directory (assuming config['log_files'][0] is a full path)
    base_log_path = config['log_files'][0]  # e.g., '/var/log/logname-20250223'
    directory = os.path.dirname(base_log_path) or '.'  # Extract directory, default to current if none
    base_log_name = base_log_path.split('/')[-1].split('-')[0]  # Extract 'logname'
    log_files = get_recent_log_files(base_log_name, directory)

    # Initialize data structures
    daily_unique_visitors = defaultdict(set)
    daily_pageviews = defaultdict(int)
    blog_post_views = defaultdict(int)
    country_visits = defaultdict(int)
    daily_rss_unique_ips = defaultdict(set)
    daily_scraper_pageviews = defaultdict(int)
    browser_counts = defaultdict(int)
    os_counts = defaultdict(int)
    utm_source_counts = defaultdict(int)
    rss_user_agent_counts = defaultdict(int)
    hourly_visits = defaultdict(int)

    # Create log parser
    parser = apachelogs.LogParser(apachelogs.COMBINED)

    # Process each log file
    for log_file in log_files:
        with open(log_file, 'r') as f:
            for line in f:
                try:
                    entry = parser.parse(line)
                    if entry.request_time.replace(tzinfo=None) < seven_days_ago:
                        continue
                    date_str = entry.request_time.strftime('%Y-%m-%d')
                    request_parts = entry.request_line.split()
                    if len(request_parts) < 2:
                        continue
                    path = urlparse(request_parts[1]).path
                    user_agent_str = entry.headers_in.get('User-Agent') or ''
                    ua = ua_parse(user_agent_str)

                    # Count browsers and OS
                    browser = ua.browser.family or "Unknown"
                    os_family = ua.os.family or "Unknown"
                    browser_counts[browser] += 1
                    os_counts[os_family] += 1

                    # Count hourly visits
                    hour = entry.request_time.hour
                    hourly_visits[hour] += 1

                    # Parse UTM sources
                    query = urlparse(request_parts[1]).query
                    if query:
                        params = parse_qs(query)
                        if 'utm_source' in params:
                            utm_source = params['utm_source'][0]
                            utm_source_counts[utm_source] += 1

                    # Skip static content
                    if any(path.startswith(ex) for ex in config['excluded_prefixes']):
                        continue

                    if ua.is_bot:
                        daily_scraper_pageviews[date_str] += 1
                    else:
                        daily_pageviews[date_str] += 1
                        daily_unique_visitors[date_str].add(entry.remote_host)
                        if any(path.startswith(bp) for bp in config['blog_post_prefixes']):
                            clean_name = clean_post_name(path, config['blog_post_prefixes'])
                            blog_post_views[clean_name] += 1
                        try:
                            country = reader.country(entry.remote_host)
                            country_name = country.country.name or "Unknown"
                        except:
                            country_name = "Unknown"
                        if country_name != "Unknown":
                            country_visits[country_name] += 1

                    # RSS feed access
                    twenty_four_hours_ago = datetime.now() - timedelta(hours=24)
                    if path in config['rss_feed_urls']:
                        if entry.request_time.replace(tzinfo=None) >= twenty_four_hours_ago:
                            rss_user_agent_counts[user_agent_str] += 1
                        if not ua.is_bot:
                            daily_rss_unique_ips[date_str].add(entry.remote_host)

                except apachelogs.InvalidEntryError:
                    continue

    # Compute final stats for the last 7 days
    today = datetime.now().date()
    dates = [today - timedelta(days=i) for i in range(7)][::-1]
    date_strs = [d.strftime('%Y-%m-%d') for d in dates]
    daily_unique_visitors_counts = {d: len(s) for d, s in daily_unique_visitors.items()}
    daily_rss_unique_counts = {d: len(s) for d, s in daily_rss_unique_ips.items()}
    daily_visitors = [daily_unique_visitors_counts.get(d, 0) for d in date_strs]
    daily_rss_unique = [daily_rss_unique_counts.get(d, 0) for d in date_strs]
    daily_scraper_pageviews_list = [daily_scraper_pageviews.get(d, 0) for d in date_strs]
    top_blog_posts = sorted(blog_post_views.items(), key=lambda x: x[1], reverse=True)[:10]
    sorted_browsers = sorted(browser_counts.items(), key=lambda x: x[1], reverse=True)
    sorted_os = sorted(os_counts.items(), key=lambda x: x[1], reverse=True)
    sorted_utm_sources = sorted(utm_source_counts.items(), key=lambda x: x[1], reverse=True)
    sorted_rss_user_agents = sorted(rss_user_agent_counts.items(), key=lambda x: x[1], reverse=True)
    hourly_data = [hourly_visits[h] for h in range(24)]
    country_data = [{'name': country, 'value': count} for country, count in country_visits.items()]

    # Prepare template context
    context = {
        'daily_dates': date_strs,
        'daily_visitors': daily_visitors,
        'daily_rss_unique': daily_rss_unique,
        'daily_scraper_pageviews': daily_scraper_pageviews_list,
        'top_posts_labels': [post for post, count in top_blog_posts],
        'top_posts_data': [count for post, count in top_blog_posts],
        'country_data': country_data,
        'browsers': sorted_browsers,
        'operating_systems': sorted_os,
        'utm_sources': sorted_utm_sources,
        'rss_user_agents': sorted_rss_user_agents,
        'hourly_data': hourly_data,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
    }

    # Render template
    env = jinja2.Environment(loader=jinja2.FileSystemLoader('.'))
    template = env.get_template('dashboard.html')
    html = template.render(context)

    # Write dashboard
    output_dir = config['output_dir']
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, 'index.html'), 'w') as f:
        f.write(html)

if __name__ == '__main__':
    main()
