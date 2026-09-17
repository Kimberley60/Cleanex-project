document.addEventListener("DOMContentLoaded", () => {
ServicesFilter.init();
});

const ServicesFilter = {


filterBar: null,

filterButtons: [],

searchInput: null,

clearSearchButton: null,

prefersReducedMotion: window.matchMedia(
    "(prefers-reduced-motion: reduce)"
).matches,


/*
 * =========================================================
 * INITIALIZATION
 * =========================================================
 */

init() {

    this.filterBar = document.querySelector(
        ".services-filter-bar"
    );

    this.searchInput = document.querySelector(
        "#service-search"
    );

    this.clearSearchButton = document.querySelector(
        "#clear-service-search"
    );


    /*
     * Collect category filter buttons if the filter bar
     * exists on the page.
     */

    if (this.filterBar) {

        this.filterButtons = Array.from(
            this.filterBar.querySelectorAll(
                ".services-filter-btn"
            )
        );

    }


    /*
     * Synchronize the interface with the current URL.
     */

    this.syncActiveButtonFromUrl();

    this.syncSearchInputFromUrl();


    /*
     * Attach UI event listeners.
     */

    this.bindEvents();

},


/*
 * =========================================================
 * URL HELPERS
 * =========================================================
 *
 * Django is responsible for filtering services.
 *
 * JavaScript only reads the URL to keep the interface
 * synchronized with the current server-rendered state.
 */


getUrlParams() {

    return new URLSearchParams(
        window.location.search
    );

},


getCategoryFromUrl() {

    const params = this.getUrlParams();

    return (
        params.get("category") || ""
    ).trim() || "all";

},


getSearchFromUrl() {

    const params = this.getUrlParams();

    return (
        params.get("search") || ""
    ).trim();

},


/*
 * =========================================================
 * CATEGORY FILTER STATE
 * =========================================================
 */

syncActiveButtonFromUrl() {

    if (!this.filterButtons.length) {
        return;
    }


    const category =
        this.getCategoryFromUrl();


    this.filterButtons.forEach((button) => {

        const buttonCategory =
            button.dataset.category || "all";

        const isActive =
            buttonCategory === category;


        button.classList.toggle(
            "active",
            isActive
        );


        if (isActive) {

            button.setAttribute(
                "aria-current",
                "page"
            );

        } else {

            button.removeAttribute(
                "aria-current"
            );

        }

    });

},


/*
 * =========================================================
 * SEARCH STATE
 * =========================================================
 */

syncSearchInputFromUrl() {

    if (!this.searchInput) {
        return;
    }


    this.searchInput.value =
        this.getSearchFromUrl();

},


/*
 * =========================================================
 * EVENT HANDLING
 * =========================================================
 */

bindEvents() {

    /*
     * -----------------------------------------------------
     * CATEGORY FILTER BUTTONS
     * -----------------------------------------------------
     *
     * These are normal Django links.
     *
     * We intentionally DO NOT use preventDefault().
     *
     * Clicking a category should navigate to a URL such as:
     *
     * /services/?category=residential
     *
     * or:
     *
     * /services/?category=residential&search=deep
     *
     * Django then performs the actual filtering.
     */

    this.filterButtons.forEach((button) => {

        button.addEventListener(
            "click",
            () => {

                this.setActiveButton(
                    button
                );

            }
        );

    });


    /*
     * -----------------------------------------------------
     * CLEAR SEARCH
     * -----------------------------------------------------
     */

    if (this.clearSearchButton) {

        this.clearSearchButton.addEventListener(
            "click",
            () => {

                this.clearSearch();

            }
        );

    }


    /*
     * -----------------------------------------------------
     * BROWSER BACK / FORWARD
     * -----------------------------------------------------
     *
     * The Services page is server-rendered, so when the
     * browser history changes we reload the page.
     */

    window.addEventListener(
        "popstate",
        () => {

            window.location.reload();

        }
    );


    /*
     * -----------------------------------------------------
     * REDUCED MOTION
     * -----------------------------------------------------
     *
     * Respect changes to the user's motion preference.
     */

    const motionQuery =
        window.matchMedia(
            "(prefers-reduced-motion: reduce)"
        );


    if (motionQuery.addEventListener) {

        motionQuery.addEventListener(
            "change",
            (event) => {

                this.prefersReducedMotion =
                    event.matches;

            }
        );

    }

},


/*
 * =========================================================
 * ACTIVE BUTTON UI
 * =========================================================
 */

setActiveButton(activeButton) {

    this.filterButtons.forEach((button) => {

        const isActive =
            button === activeButton;


        button.classList.toggle(
            "active",
            isActive
        );


        if (isActive) {

            button.setAttribute(
                "aria-current",
                "page"
            );

        } else {

            button.removeAttribute(
                "aria-current"
            );

        }

    });

},


/*
 * =========================================================
 * CLEAR SEARCH
 * =========================================================
 *
 * Only removes the search parameter.
 *
 * Example:
 *
 * /services/?category=residential&search=deep
 *
 * becomes:
 *
 * /services/?category=residential
 *
 * The selected category is preserved.
 */

clearSearch() {

    const url = new URL(
        window.location.href
    );


    url.searchParams.delete(
        "search"
    );


    window.location.href =
        url.toString();

}


};
