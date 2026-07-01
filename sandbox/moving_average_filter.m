% filepath: sandbox/moving_average_filter.m

function filtered_signal = moving_average_filter(signal_vector, window_size)
    % Check if input is a 1D array and has at least one element
    if ~isvector(signal_vector) || isempty(signal_vector)
        error('Input must be a non-empty 1D array.');
    end
    
    % Check if window size is a positive integer
    if ~isscalar(window_size) || window_size <= 0
        error('Window size must be a positive integer.');
    end
    
    % Calculate the number of elements to consider in each window
    num_elements = length(signal_vector);
    
    % Initialize the filtered signal array with zeros
    filtered_signal = zeros(1, num_elements - window_size + 1);
    
    % Compute the moving average for each window
    for i = 1:num_elements - window_size + 1
        window = signal_vector(i:i+window_size-1);
        filtered_signal(i) = sum(window) / window_size;
    end
    
    % Return the filtered signal
end

% Test script for moving_average_filter
addpath('.');
result = moving_average_filter([1, 2, 3, 4, 5], 3);
assert(abs(result - [1, 2, 3]) < 1e-9, 'Test 1 failed');
disp('All tests passed.');