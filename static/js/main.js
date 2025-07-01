document.addEventListener('DOMContentLoaded', function () {
    var navbar = document.getElementById('navbar');
    var lastScrollTop = 0;
    
    // Check if we're on the index.html page
    var isIndexPage = window.location.pathname.endsWith('index.html') || 
                      window.location.pathname === '/';
    
    // For non-index pages, show navbar permanently with no animation
    if (!isIndexPage) {
        navbar.classList.add('translate-y-0', 'opacity-100');
        navbar.classList.remove('transition-all', 'duration-300', 'ease-in-out');
        return;
    }

    // Add transition properties for index.html
    navbar.classList.add('transition-all', 'duration-300', 'ease-in-out');
    
    // Initially hide navbar on index.html
    navbar.classList.add('-translate-y-full', 'opacity-0');
    navbar.classList.remove('translate-y-0', 'opacity-100');

    // Only apply scroll behavior for index.html
    window.addEventListener('scroll', function () {
        var scrollTop = window.scrollY || document.documentElement.scrollTop;

        if (scrollTop > lastScrollTop && scrollTop > 50) {
            // Downward scroll past 50px - hide navbar
            navbar.classList.remove('translate-y-0', 'opacity-100');
            navbar.classList.add('-translate-y-full', 'opacity-0');
        } else if (scrollTop < lastScrollTop) {
            // Upward scroll - show navbar
            navbar.classList.add('translate-y-0', 'opacity-100');
            navbar.classList.remove('-translate-y-full', 'opacity-0');
        }
        
        // If at top of page, hide navbar
        if (scrollTop === 0) {
            navbar.classList.remove('translate-y-0', 'opacity-100');
            navbar.classList.add('-translate-y-full', 'opacity-0');
        }
        
        lastScrollTop = scrollTop;
    });
});