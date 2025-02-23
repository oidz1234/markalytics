import json
from datetime import datetime, timedelta
from collections import defaultdict
import apachelogs
import geoip2.database
import jinja2
import os
import re
import glob
from urllib.parse import urlparse, unquote, parse_qs
from user_agents import parse as ua_parse
from concurrent.futures import ThreadPoolExecutor
import time

def clean_post_name(path, prefixes):
    for prefix in prefixes:
        if path.startswith(prefix):
            post_name = path[len(prefix):]
            return unquote(post_name)
    return path

def get_date_range(days_back=7):
    """Generate date strings for the last 7 days in Apache log format."""
    today = datetime.now().date()
    dates = [today - timedelta(days=i) for i in range(days_back)][::-1]
    date_patterns = [d.strftime(r'\[%d/%b/%Y') for d in dates]
    return dates, date_patterns

def process_log_file(log_file, date_patterns, seven_days_ago, geoip_db_path):
    """Process a single log file, returning stats for matching entries."""
    local_unique_visitors = defaultdict(set)
    local_pageviews = defaultdict(int)
    local_blog_views = defaultdict(int)
    local_country_visits = defaultdict(int)
    local_rss_ips = defaultdict(set)
    local_scraper_views = defaultdict(int)
    local_browser_counts = defaultdict(int)
    local_os_counts = defaultdict(int)
    local_utm_counts = defaultdict(int)
    local_rss_ua_counts = defaultdict(int)
    local_hourly_visits = defaultdict(int)

    date_regex = '|'.join(date_patterns)
    full_pattern = rf"({date_regex}:\d{{2}}:\d{{2}}:\d{{2}} \+\d{{4}})"
    log_entry_regex = re.compile(full_pattern)
    
    parser = apachelogs.LogParser(apachelogs.COMBINED)
    reader = geoip2.database.Reader(geoip_db_path)  # Use config path
    
    with open(log_file, 'r') as f:
        chunk_size = 65536  # 64KB
        buffer = ""
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                if buffer:
                    process_buffer(buffer, log_entry_regex, parser, reader,
                                 local_unique_visitors, local_pageviews, local_blog_views,
                                 local_country_visits, local_rss_ips, local_scraper_views,
                                 local_browser_counts, local_os_counts, local_utm_counts,
                                 local_rss_ua_counts, local_hourly_visits, seven_days_ago)
                break
            buffer += chunk
            lines = buffer.split('\n')
            buffer = lines[-1]
            for line in lines[:-1]:
                process_buffer(line, log_entry_regex, parser, reader,
                              local_unique_visitors, local_pageviews, local_blog_views,
                              local_country_visits, local_rss_ips, local_scraper_views,
                              local_browser_counts, local_os_counts, local_utm_counts,
                              local_rss_ua_counts, local_hourly_visits, seven_days_ago)
    
    return (local_unique_visitors, local_pageviews, local_blog_views, local_country_visits,
            local_rss_ips, local_scraper_views, local_browser_counts, local_os_counts,
            local_utm_counts, local_rss_ua_counts, local_hourly_visits)

def process_buffer(line, regex, parser, reader, unique_visitors, pageviews, blog_views,
                  country_visits, rss_ips, scraper_views, browser_counts, os_counts,
                  utm_counts, rss_ua_counts, hourly_visits, seven_days_ago):
    """Helper to process a single line if it matches the regex."""
    if regex.search(line):
        try:
            entry = parser.parse(line)
            if entry.request_time.replace(tzinfo=None) < seven_days_ago:
                return
            date_str = entry.request_time.strftime('%Y-%m-%d')
            request_parts = entry.request_line.split()
            if len(request_parts) < 2:
                return
            path = urlparse(request_parts[1]).path
            user_agent_str = entry.headers_in.get('User-Agent') or ''
            ua = ua_parse(user_agent_str)

            browser = ua.browser.family or "Unknown"
            os_family = ua.os.family or "Unknown"
            browser_counts[browser] += 1
            os_counts[os_family] += 1
            hour = entry.request_time.hour
            hourly_visits[hour] += 1

            query = urlparse(request_parts[1]).query
            if query:
                params = parse_qs(query)
                if 'utm_source' in params:
                    utm_counts[params['utm_source'][0]] += 1

            if any(path.startswith(ex) for ex in ['/static/']):  # Placeholder, replace with config
                return

            if ua.is_bot:
                scraper_views[date_str] += 1
            else:
                pageviews[date_str] += 1
                unique_visitors[date_str].add(entry.remote_host)
                if any(path.startswith(bp) for bp in ['/blog/']):  # Placeholder, replace with config
                    clean_name = clean_post_name(path, ['/blog/'])
                    blog_views[clean_name] += 1
                try:
                    country = reader.country(entry.remote_host)
                    country_name = country.country.name or "Unknown"
                    if country_name != "Unknown":
                        country_visits[country_name] += 1
                except:
                    pass

            twenty_four_hours_ago = datetime.now() - timedelta(hours=24)
            if path in ['/rss/']:  # Placeholder, replace with config
                if entry.request_time.replace(tzinfo=None) >= twenty_four_hours_ago:
                    rss_ua_counts[user_agent_str] += 1
                if not ua.is_bot:
                    rss_ips[date_str].add(entry.remote_host)
        except apachelogs.InvalidEntryError:
            pass

def merge_stats(stats_list):
    """Merge stats from multiple threads."""
    merged = [defaultdict(set), defaultdict(int), defaultdict(int), defaultdict(int),
              defaultdict(set), defaultdict(int), defaultdict(int), defaultdict(int),
              defaultdict(int), defaultdict(int), defaultdict(int)]
    for stats in stats_list:
        for i, (src, dst) in enumerate(zip(stats, merged)):
            if isinstance(src, defaultdict) and isinstance(src, type(dst)):
                if isinstance(dst, defaultdict) and dst.default_factory == set:
                    for k, v in src.items():
                        dst[k].update(v)
                else:
                    for k, v in src.items():
                        dst[k] += v
    return merged

def main():
    start_time = time.time()
    with open('config.json') as f:
        config = json.load(f)

    geoip_db_path = config['geoip_db']  # Use the path from your config
    base_log_path = config['log_files'][0]
    directory = os.path.dirname(base_log_path) or '.'
    seven_days_ago_timestamp = (datetime.now() - timedelta(days=6)).timestamp()
    
    log_files = [f for f in glob.glob(os.path.join(directory, "django_access*"))
                 if os.path.getmtime(f) >= seven_days_ago_timestamp]
    print(f"Filtered files: {log_files}")

    dates, date_patterns = get_date_range()
    seven_days_ago = datetime.now() - timedelta(days=6)

    with ThreadPoolExecutor() as executor:
        futures = [executor.submit(process_log_file, log_file, date_patterns, seven_days_ago, geoip_db_path)
                   for log_file in log_files]
        results = [future.result() for future in futures]

    (daily_unique_visitors, daily_pageviews, blog_post_views, country_visits,
     daily_rss_unique_ips, daily_scraper_pageviews, browser_counts, os_counts,
     utm_source_counts, rss_user_agent_counts, hourly_visits) = merge_stats(results)

    date_strs = [d.strftime('%Y-%m-%d') for d in dates]
    daily_visitors = [len(daily_unique_visitors.get(d, set())) for d in date_strs]
    daily_rss_unique = [len(daily_rss_unique_ips.get(d, set())) for d in date_strs]
    daily_scraper_pageviews_list = [daily_scraper_pageviews.get(d, 0) for d in date_strs]
    top_blog_posts = sorted(blog_post_views.items(), key=lambda x: x[1], reverse=True)[:10]
    sorted_browsers = sorted(browser_counts.items(), key=lambda x: x[1], reverse=True)
    sorted_os = sorted(os_counts.items(), key=lambda x: x[1], reverse=True)
    sorted_utm_sources = sorted(utm_source_counts.items(), key=lambda x: x[1], reverse=True)
    sorted_rss_user_agents = sorted(rss_user_agent_counts.items(), key=lambda x: x[1], reverse=True)
    hourly_data = [hourly_visits[h] for h in range(24)]
    country_data = [{'name': k, 'value': v} for k, v in country_visits.items()]

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

    env = jinja2.Environment(loader=jinja2.FileSystemLoader('.'))
    template = env.get_template('dashboard.html')
    html = template.render(context)

    output_dir = config['output_dir']
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, 'index.html'), 'w') as f:
        f.write(html)

    print(f"Completed in {time.time() - start_time:.2f} seconds")

if __name__ == '__main__':
    main()
