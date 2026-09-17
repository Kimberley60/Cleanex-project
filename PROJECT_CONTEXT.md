# CLEANEX Project Context

## Project Overview

CLEANEX is a professional cleaning services web application built with Django.

## Technology Stack

* Django
* Python
* Bootstrap 5
* Custom CSS
* JavaScript
* PostgreSQL

## Project Goals

Create a modern, responsive, and professional cleaning services platform.

## Development Rules

* Use Bootstrap 5 , writing custom CSS.
* Follow mobile-first responsive design.
* Keep templates modular and reusable.
* Use Django template inheritance.
* Reuse components with `{% include %}` whenever possible.
* Use backend-first architecture: building the database, business logic, and APIs first, then implementing the frontend with Bootstrap and CSS for consistent UI styling later after completing the backend-first architecture.
* Keep business logic inside views and models, not templates.
* Use class-based views where appropriate.
* Follow Django best practices.
* Allow review if possible before making final changes.

## Folder Conventions

templates/
    base/
        base.html
    components/
        booking_form.html
        footer.html
        navbar.html
        quote_form.html
        service_card.html
        testimonial_card.html
        why_choose.html
    includes/
        hero.html
        how_it_works.html
        cta_section.html

```
pages/
    booking.html
    booking_success.html
    about.html
    services.html
    contact.html
    gallery.html
    home.html
    quote.html
    
```
    services/
        service_detail.html
        service_list.html
        


## UI Guidelines

* Professional cleaning company branding.
* Clean, modern appearance.
* Consistent spacing and typography in main.css.
* Responsive on desktop, tablet, and mobile.
* Accessibility friendly.

## Code Quality

* Avoid duplicate code.
* Write reusable components.
* Keep code readable and maintainable.
* Add comments only when necessary.
